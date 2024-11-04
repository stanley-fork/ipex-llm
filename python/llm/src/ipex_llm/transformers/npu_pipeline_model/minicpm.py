#
# Copyright 2016 The BigDL Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


import torch
import numpy as np
import os
from .common import update_names_of_IR_and_export_blob
from intel_npu_acceleration_library.backend.factory import NNFactory
from ipex_llm.transformers.npu_models.mp_models_base import LLMBaseNNFactory
from typing import Sequence


class MiniCPMEmbedding(NNFactory):
    def __init__(
        self,
        vocab_size,
        embedding_dim,
        embedding_weight,
        padding_idx,
        dtype,  # fp16
        scale_emb,
        device: str = "NPU",
    ):
        super().__init__(False, device)
        self.vocab_size = vocab_size
        self.embedding_dim = embedding_dim
        self.padding_idx = padding_idx
        self.dtype = dtype

        # define input
        weight = self.constant(embedding_weight)
        input = self.parameter((1, 1), dtype=np.int32)

        if padding_idx == -1:
            padding_idx += vocab_size

        axis_node = self.constant(np.array([0], dtype=np.int64))
        if padding_idx is not None:
            masked_embeddings = np.ones(weight.shape, dtype=np.float16)
            masked_embeddings[padding_idx, :] = 0.0  # mask

            node_mask = self.constant(masked_embeddings)
            node_masked_w = self.eltwise_mul(weight, node_mask)
            res = self.gather(node_masked_w, input, axis_node, 0)
        else:
            res = self.gather(weight, input, axis_node, 0)
        res = res * scale_emb

        # define outputs
        res = self.convert_to_fp16(res)

        print("start compiling")
        self.compile()


class MiniCPMLMHead(LLMBaseNNFactory):
    def __init__(
        self,
        hidden_shape: Sequence[int],
        num_heads: int,
        rms_norm_eps: float,
        model_norm_weight,
        vocab_size: int,
        mode: str = "decode",
        dtype: np.dtype = np.int8,
        max_seq_len: int = 1024,
        transpose_value: bool = False,
        profile: bool = False,
        device: str = "NPU",
    ):
        super().__init__(max_seq_len=max_seq_len,
                         transpose_value=transpose_value,
                         dtype=dtype,
                         profile=profile,
                         device=device)
        self.max_seq_len = max_seq_len
        self.dtype = dtype
        self.batch_size, self.seq_len, self.hidden_size = hidden_shape
        self.mode = mode
        self.rms_norm_eps = rms_norm_eps
        self.transpose_value = transpose_value
        self.vocab_size = vocab_size

        self.num_heads = num_heads
        self.head_dim = self.hidden_size // self.num_heads

        # define input, the order self.parameter matters
        input = self.create_input_op((self.batch_size, self.seq_len, self.hidden_size))

        hidden_states = input

        # model norm and lm head
        model_norm_weight = self.constant(model_norm_weight)
        hidden_states = self.layer_norm(hidden_states, model_norm_weight)
        if vocab_size == 122753:
            # for MiniCPM-2B-sft-bf16
            hidden_states_1 = self.linear(
                hidden_states, 73440, self.hidden_size, bias=False, wt_dtype=self.dtype
            )
            hidden_states_2 = self.linear(
                hidden_states, 73440, self.hidden_size, bias=False, wt_dtype=self.dtype
            )
            hidden_states_2 = self.slice(hidden_states_2, begin=[0, 0, 0], end=[1, 1, 49313])
            hidden_states = self.concat(hidden_states_1, hidden_states_2, axis=2)
        else:
            # for MiniCPM-1B-sft-bf16
            hidden_states = self.linear(
                hidden_states, self.vocab_size, self.hidden_size, bias=False, wt_dtype=self.dtype
            )

        # define outputs
        hidden_states = self.convert_to_fp32(hidden_states)

        print("start compiling")
        self.compile()


def convert_lm_head_and_embedding(model, n_splits_linear, temp_dir, weight_dir):
    num_heads = model.model.layers[0].self_attn.num_heads
    num_key_value_heads = model.model.layers[0].self_attn.num_key_value_heads
    head_dim = model.model.layers[0].self_attn.head_dim
    rms_norm_eps = model.config.rms_norm_eps
    vocab_size = model.config.vocab_size
    model_norm = model.model.norm
    if n_splits_linear == 1:
        if vocab_size == 122753:
            # for MiniCPM-2B-sft-bf16
            weights = [(model.lm_head_0.weight, model.lm_head_0.scale),
                       (model.lm_head_1.weight, model.lm_head_1.scale)]
        else:
            # for MiniCPM-1B-sft-bf16
            weights = [(model.lm_head.weight, model.lm_head.scale)]
    else:
        # TODO
        pass
    if isinstance(weights[0], tuple):
        np_dtype = np.int8 if weights[0][0].dtype == torch.int8 else np.uint8
    else:  # FP16 Linear
        np_dtype = np.float16

    new_lm_head = MiniCPMLMHead(
        [1, 1, num_heads * head_dim],
        num_heads=num_heads,
        max_seq_len=1,
        rms_norm_eps=rms_norm_eps,
        mode="decode",
        transpose_value=False,
        dtype=np_dtype,
        model_norm_weight=model_norm.weight.to(torch.float16),
        vocab_size=vocab_size,
    )
    last_blob_path = update_names_of_IR_and_export_blob(new_lm_head, "lm_head", temp_dir)

    # save weights bins files
    if n_splits_linear == 1:
        if vocab_size == 122753:
            weight_numpy = [model.lm_head_0.weight.data.numpy(),
                            model.lm_head_0.scale.data.numpy(),
                            model.lm_head_1.weight.data.numpy(),
                            model.lm_head_1.scale.data.numpy(), ]
        else:
            weight_numpy = [model.lm_head.weight.data.numpy(), model.lm_head.scale.data.numpy(), ]
    else:
        # TODO
        pass

    for idx, weight in enumerate(weight_numpy):
        bin_file = os.path.join(weight_dir, f"model_lm_head_input_{1+idx}.bin")
        weight.tofile(bin_file)

    embedding_layer = model.model.embed_tokens
    new_embedding = MiniCPMEmbedding(
        vocab_size=model.config.vocab_size,
        embedding_dim=model.config.hidden_size,
        embedding_weight=embedding_layer.weight.to(torch.float16).detach().numpy(),
        padding_idx=model.config.pad_token_id,
        dtype=np.float16,
        scale_emb=model.config.scale_emb,
    )
    first_blob_path = update_names_of_IR_and_export_blob(new_embedding, "embedding",
                                                         temp_dir)
    return first_blob_path, last_blob_path


def convert_minicpm_layer(model, layer_idx, n_splits_linear, n_splits_down_proj,
                          temp_dir, weight_dir, transpose_value_cache, kv_len, group_size):
    num_heads = model.model.layers[0].self_attn.num_heads
    num_key_value_heads = model.model.layers[0].self_attn.num_key_value_heads
    head_dim = model.model.layers[0].self_attn.head_dim
    intermediate_size = model.config.intermediate_size
    rms_norm_eps = model.config.rms_norm_eps
    num_hidden_layers = model.config.num_hidden_layers
    scale_depth = model.model.config.scale_depth

    from ipex_llm.transformers.npu_models.minicpm_mp import LowBitMinicpmMultiDecoderlayer
    curr_layer = model.model.layers[layer_idx]
    attn_layer = curr_layer.self_attn
    mlp_layer = curr_layer.mlp

    weights = []
    if n_splits_linear == 1:
        weights = [
            (attn_layer.q_proj.weight, attn_layer.q_proj.scale),
            (attn_layer.k_proj.weight, attn_layer.k_proj.scale),
            (attn_layer.v_proj.weight, attn_layer.v_proj.scale),
            (attn_layer.o_proj.weight, attn_layer.o_proj.scale),
            (mlp_layer.gate_proj.weight, mlp_layer.gate_proj.scale),
            (mlp_layer.up_proj.weight, mlp_layer.up_proj.scale),
            (mlp_layer.down_proj.weight, mlp_layer.down_proj.scale),
        ]
    else:
        # TODO
        pass

    cached_cos = curr_layer.self_attn.rotary_emb.cos_cached.to(torch.float16)
    cached_sin = curr_layer.self_attn.rotary_emb.sin_cached.to(torch.float16)
    layer_norm_0 = curr_layer.input_layernorm.weight.to(torch.float16)
    layer_norm_1 = curr_layer.post_attention_layernorm.weight.to(torch.float16)

    if isinstance(weights[0], tuple):
        np_dtype = np.int8 if weights[0][0].dtype == torch.int8 else np.uint8
    else:  # FP16 Linear
        np_dtype = np.float16

    single_decoder = LowBitMinicpmMultiDecoderlayer(
        [1, 1, num_heads * head_dim],
        input_layernorm_weights=[layer_norm_0],
        post_attn_layernorm_weights=[layer_norm_1],
        cached_cos=cached_cos,
        cached_sin=cached_sin,
        num_heads=num_heads,
        num_key_value_heads=num_key_value_heads,
        num_layers=1,
        max_seq_len=kv_len,
        rms_norm_eps=rms_norm_eps,
        intermediate_size=intermediate_size,
        scale_depth=scale_depth,
        num_hidden_layers=num_hidden_layers,
        mode="decode",
        transpose_value=transpose_value_cache,
        dtype=np_dtype,
    )
    rest_blob_path = update_names_of_IR_and_export_blob(single_decoder,
                                                        f"decoder_layer_{layer_idx}",
                                                        temp_dir)

    for idx, (weight, scale) in enumerate(weights):
        bin_file = os.path.join(weight_dir, f"model_{layer_idx}_input_{5+idx*2}.bin")
        weight.numpy().tofile(bin_file)
        bin_file = os.path.join(weight_dir, f"model_{layer_idx}_input_{5+idx*2+1}.bin")
        scale.numpy().tofile(bin_file)
    del single_decoder