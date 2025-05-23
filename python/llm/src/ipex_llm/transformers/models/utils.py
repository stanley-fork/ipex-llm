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

import os
import torch
import warnings
from ipex_llm.utils.common import invalidInputError
from ipex_llm.ggml.quantize import ggml_tensor_qtype
from ipex_llm.transformers.utils import get_xpu_device_name
from ipex_llm.transformers.low_bit_linear import SYM_INT4, SYM_INT8, FP8E5, IQ2_XXS, FP4, FP8E4,\
    FP6, ASYM_INT4, WOQ_INT4

FP8_KV_ALLOC_LENGTH = 512
KV_CACHE_ALLOC_BLOCK_LENGTH = int(os.environ.get("KV_CACHE_ALLOC_BLOCK_LENGTH", 256))

# used in fused mlp forward
SILU = 0
GELU = 1


def decoding_fast_path_qtype_check(proj):
    qtype = getattr(proj, "qtype", None)
    return qtype in [SYM_INT4, FP8E5, FP4, WOQ_INT4]


def init_kv_cache(batch_size, num_heads, head_dim, current_length, max_length, dtype, device):
    key_cache_storage = torch.empty(batch_size, num_heads,
                                    max_length, head_dim,
                                    dtype=dtype, device=device)
    value_cache_storage = torch.empty(batch_size, num_heads,
                                      max_length, head_dim,
                                      dtype=dtype, device=device)

    key_cache = key_cache_storage.as_strided((batch_size, num_heads,
                                              current_length, head_dim),
                                             key_cache_storage.stride(),
                                             storage_offset=0)
    value_cache = value_cache_storage.as_strided((batch_size, num_heads,
                                                  current_length, head_dim),
                                                 value_cache_storage.stride(),
                                                 storage_offset=0)
    return key_cache, value_cache


def extend_kv_cache(batch_size, num_heads, head_dim, current_length, max_length, dtype, device):
    # empty cache to reduce gpu memory
    if device.type == 'xpu':
        torch.xpu.empty_cache()
    return init_kv_cache(batch_size, num_heads, head_dim, current_length, max_length, dtype, device)


def append_kv_cache(cache_k, cache_v, key_states, value_states):
    new_size = (cache_k.size(0),
                cache_k.size(1),
                cache_k.size(2) + key_states.size(2),
                cache_k.size(3))
    new_cache_k = cache_k.as_strided(new_size, cache_k.stride(), storage_offset=0)
    new_cache_k[:, :, cache_k.size(2):cache_k.size(2) + key_states.size(2), :] = key_states
    new_cache_v = cache_v.as_strided(new_size, cache_v.stride(), storage_offset=0)
    new_cache_v[:, :, cache_v.size(2):cache_v.size(2) + key_states.size(2), :] = value_states
    return new_cache_k, new_cache_v


def use_quantize_kv_cache(linear: torch.nn.Module, x: torch.Tensor,
                          num_heads: int, num_kv_heads: int) -> bool:
    if os.environ.get("BIGDL_QUANTIZE_KV_CACHE", None) is not None:
        warnings.warn(
            "`BIGDL_QUANTIZE_KV_CACHE` is deprecated and will be removed in future releases. "
            "Please use `IPEX_LLM_QUANTIZE_KV_CACHE` instead."
        )
        return os.environ["BIGDL_QUANTIZE_KV_CACHE"] == "1"
    elif os.environ.get("IPEX_LLM_QUANTIZE_KV_CACHE", None) is not None:
        return os.environ["IPEX_LLM_QUANTIZE_KV_CACHE"] == "1"
    elif os.environ.get("IPEX_LLM_LOW_MEM", None) is not None:
        return os.environ["IPEX_LLM_LOW_MEM"] == "1"
    elif linear.weight.dtype != torch.uint8:    # unquantized
        return False
    else:
        device_name = get_xpu_device_name(x.device)
        return (
            num_kv_heads >= 4
            and (
                device_name in ["mtl", "lnl", "arl"] and num_heads // num_kv_heads <= 4
                or device_name in ["arc", "bmg"] and x.size(0) > 1
            )
        )


def init_fp8_kv_cache(batch_size, num_heads, current_length, head_dim, device):
    max_length = current_length + FP8_KV_ALLOC_LENGTH

    k_cache_storage = torch.empty(batch_size, num_heads, max_length, head_dim,
                                  dtype=torch.uint8, device=device)
    k_cache = k_cache_storage.as_strided((batch_size, num_heads, 0, head_dim),
                                         k_cache_storage.stride(), storage_offset=0)

    v_cache_storage = torch.empty(batch_size, num_heads, max_length, head_dim,
                                  dtype=torch.uint8, device=device)
    v_cache = v_cache_storage.as_strided((batch_size, num_heads, 0, head_dim),
                                         v_cache_storage.stride(), storage_offset=0)
    return k_cache, v_cache


def append_fp8_kv_cache(k_cache, v_cache, key, value):
    batch_size, num_heads, cur_length, head_dim = k_cache.shape
    new_length = cur_length + key.size(2)
    new_size = (batch_size, num_heads, new_length, head_dim)

    if k_cache.stride(1) < new_length * k_cache.size(3):
        new_k_cache, new_v_cache = init_fp8_kv_cache(batch_size, num_heads, new_length,
                                                     head_dim, key.device)
        new_k_cache = new_k_cache.as_strided(new_size, new_k_cache.stride(), storage_offset=0)
        new_v_cache = new_v_cache.as_strided(new_size, new_v_cache.stride(), storage_offset=0)
        new_k_cache[:, :, :cur_length, :] = k_cache
        new_v_cache[:, :, :cur_length, :] = v_cache
    else:
        new_k_cache = k_cache.as_strided(new_size, k_cache.stride(), storage_offset=0)
        new_v_cache = v_cache.as_strided(new_size, v_cache.stride(), storage_offset=0)

    import xe_addons
    xe_addons.quantize_key_value(key, value,
                                 new_k_cache[:, :, cur_length:new_length, :],
                                 new_v_cache[:, :, cur_length:new_length, :])

    return new_k_cache, new_v_cache


def init_unbalanced_fp8_kv_cache(batch_size, num_heads, current_length,
                                 k_head_dim, v_head_dim, device):
    # for case which k head dim is different from v head dim
    max_length = current_length + FP8_KV_ALLOC_LENGTH

    k_cache_storage = torch.empty(batch_size, num_heads, max_length, k_head_dim,
                                  dtype=torch.uint8, device=device)
    k_cache = k_cache_storage.as_strided((batch_size, num_heads, 0, k_head_dim),
                                         k_cache_storage.stride(), storage_offset=0)

    v_cache_storage = torch.empty(batch_size, num_heads, max_length, v_head_dim,
                                  dtype=torch.uint8, device=device)
    v_cache = v_cache_storage.as_strided((batch_size, num_heads, 0, v_head_dim),
                                         v_cache_storage.stride(), storage_offset=0)
    return k_cache, v_cache


def append_unbalanced_fp8_kv_cache(k_cache, v_cache, key, value):
    batch_size, num_heads, cur_length, k_head_dim = k_cache.shape
    _, _, _, v_head_dim = v_cache.shape
    new_length = cur_length + key.size(2)
    new_k_size = (batch_size, num_heads, new_length, k_head_dim)
    new_v_size = (batch_size, num_heads, new_length, v_head_dim)

    if k_cache.stride(1) < new_length * k_cache.size(3):
        new_k_cache, new_v_cache = init_unbalanced_fp8_kv_cache(batch_size, num_heads, new_length,
                                                                k_head_dim, v_head_dim, key.device)
        new_k_cache = new_k_cache.as_strided(new_k_size, new_k_cache.stride(), storage_offset=0)
        new_v_cache = new_v_cache.as_strided(new_v_size, new_v_cache.stride(), storage_offset=0)
        new_k_cache[:, :, :cur_length, :] = k_cache
        new_v_cache[:, :, :cur_length, :] = v_cache
    else:
        new_k_cache = k_cache.as_strided(new_k_size, k_cache.stride(), storage_offset=0)
        new_v_cache = v_cache.as_strided(new_v_size, v_cache.stride(), storage_offset=0)

    import xe_addons
    xe_addons.quantize_key_value(key, value,
                                 new_k_cache[:, :, cur_length:new_length, :],
                                 new_v_cache[:, :, cur_length:new_length, :])

    return new_k_cache, new_v_cache


def restore_fp8_kv_cache(k_cache, v_cache, dtype):
    key_states = torch.empty(k_cache.shape, device=k_cache.device, dtype=dtype)
    value_states = torch.empty(v_cache.shape, device=v_cache.device, dtype=dtype)

    import xe_addons
    xe_addons.dequantize_key_value(k_cache, v_cache, key_states, value_states)

    return key_states, value_states


def rotate_half(x):
    """Rotates half the hidden dims of the input."""
    x1 = x[..., :x.shape[-1] // 2]
    x2 = x[..., x.shape[-1] // 2:]
    return torch.cat((-x2, x1), dim=-1)


def rotate_every_two(x):
    x1 = x[:, :, :, ::2]
    x2 = x[:, :, :, 1::2]
    x = torch.stack((-x2, x1), dim=-1)
    return x.flatten(-2)  # in einsum notation: rearrange(x, '... d j -> ... (d j)')


def should_use_fuse_rope(hidden_states, position_ids, training):
    return (
        hidden_states.device.type == "xpu"
        and not training and not hidden_states.requires_grad
        and position_ids is not None
    )


def apply_rotary_pos_emb(q, k, cos, sin, position_ids, model_family):
    if model_family in ["llama", "baichuan", "internlm", "aquila", "gpt_neox", "mistral",
                        "qwen2", "yuan", "stablelm", "qwen2_moe"]:
        # The first two dimensions of cos and sin are always 1, so we can `squeeze` them.
        cos = cos.squeeze(1).squeeze(0)  # [seq_len, dim]
        sin = sin.squeeze(1).squeeze(0)  # [seq_len, dim]
        cos = cos[position_ids].unsqueeze(1)  # [bs, 1, seq_len, dim]
        sin = sin[position_ids].unsqueeze(1)  # [bs, 1, seq_len, dim]
        q_embed = (q * cos) + (rotate_half(q) * sin)
        k_embed = (k * cos) + (rotate_half(k) * sin)
        return q_embed, k_embed
    elif model_family == "llama2":
        cos = cos.unsqueeze(1)
        sin = sin.unsqueeze(1)
        q_embed = (q * cos) + (rotate_half(q) * sin)
        k_embed = (k * cos) + (rotate_half(k) * sin)
        return q_embed, k_embed
    elif model_family in ["chatglm"]:
        q_embed = (q * cos) + (rotate_every_two(q) * sin)
        k_embed = (k * cos) + (rotate_every_two(k) * sin)
        return q_embed, k_embed
    else:
        invalidInputError(False,
                          f"{model_family} is not supported.")


def is_enough_kv_cache_room_4_36(past_key_value, idx, seq_len=1):
    # to determinate if is enough kv cache room in transformers==4.36
    # seq_len for current seq len
    # For llama like kv cache, i.e., [bs, n_head, seq_len, head_dim]
    return past_key_value is not None and len(past_key_value.key_cache) > idx and \
        past_key_value.key_cache[idx].stride()[1] >= \
        (past_key_value.key_cache[idx].size(2) + seq_len) * \
        past_key_value.key_cache[idx].size(3)


def is_enough_kv_cache_room_4_31(past_key_value, seq_len=1):
    # to determinate if is enough kv cache room in transformers between 4.31 and 4.35
    # seq_len for current seq len
    # For llama like kv cache, i.e., [bs, n_head, seq_len, head_dim]
    return past_key_value is not None and \
        past_key_value[0].stride()[1] >= \
        (past_key_value[0].size(2) + seq_len) * past_key_value[0].size(3)


def use_sdp(q_len, kv_len, head_dim, query_states):
    return (
        query_states.device.type == "xpu"
        and query_states.dtype in [torch.float, torch.half]     # fp32/fp16
        and head_dim in [-1, 64, 80, 96, 128]
        and q_len != kv_len     # next token
        and q_len <= 32         # lookup
    )


def use_sdp_causal(q_len, kv_len, head_dim, query_states, training):
    return (
        q_len == kv_len     # first token
        and head_dim in [-1, 64, 80, 96, 128]           # for now
        and query_states.device.type == "xpu"           # GPU
        and query_states.dtype in [torch.float, torch.half]     # fp32/fp16
        and not query_states.requires_grad and not training     # not training
    )


def use_sdp_non_causal(head_dim, device, dtype):
    return (
        head_dim in [64, 80, 128]
        and device.type == "xpu"                # GPU
        and dtype in [torch.float, torch.half]  # fp32/fp16
    )


def mlp_fusion_check(x, qtype, training):
    if x.numel() // x.size(-1) != 1:
        return False
    if x.device.type != 'xpu':
        return False
    if qtype not in [SYM_INT4, FP8E5, FP4, IQ2_XXS, FP6, WOQ_INT4]:
        return False
    if training or x.requires_grad:
        return False
    if qtype == FP6:
        device = get_xpu_device_name(x.device)
        if device in ["mtl", "lnl", "arl"]:
            return False
    return True


def repeat_kv(hidden_states: torch.Tensor, n_rep: int) -> torch.Tensor:
    batch, num_key_value_heads, slen, head_dim = hidden_states.shape
    if n_rep == 1:
        return hidden_states
    hidden_states = hidden_states[:, :, None, :, :].expand(batch, num_key_value_heads,
                                                           n_rep, slen, head_dim)
    return hidden_states.reshape(batch, num_key_value_heads * n_rep, slen, head_dim)


def update_past_key_value(past_key_value, key_states, value_states,
                          kv_seq_len, use_quantize_kv, device):
    bsz, num_heads, _, head_dim = key_states.shape
    if use_quantize_kv:
        if past_key_value is None:
            k_cache, v_cache = init_fp8_kv_cache(
                bsz, num_heads, kv_seq_len, head_dim,
                device=device
            )
        else:
            k_cache, v_cache = past_key_value
        key_states, value_states = append_fp8_kv_cache(k_cache, v_cache,
                                                       key_states, value_states)
    else:
        if past_key_value is None:
            max_cache_length = kv_seq_len + KV_CACHE_ALLOC_BLOCK_LENGTH
            k_cache, v_cache = init_kv_cache(bsz,
                                             num_heads,
                                             head_dim,
                                             kv_seq_len,
                                             max_cache_length,
                                             dtype=key_states.dtype,
                                             device=device)
            k_cache[...] = key_states
            v_cache[...] = value_states
            key_states = k_cache
            value_states = v_cache
        else:
            k_cache, v_cache = past_key_value
            if k_cache.stride(1) < kv_seq_len * k_cache.size(3):
                max_cache_length = kv_seq_len + KV_CACHE_ALLOC_BLOCK_LENGTH
                new_k_cache, new_v_cache = extend_kv_cache(bsz,
                                                           num_heads,
                                                           head_dim,
                                                           k_cache.size(2),
                                                           max_cache_length,
                                                           dtype=k_cache.dtype,
                                                           device=device)
                new_k_cache[...] = k_cache
                new_v_cache[...] = v_cache
                k_cache = new_k_cache
                v_cache = new_v_cache
            key_states, value_states = append_kv_cache(k_cache, v_cache, key_states, value_states)
    return key_states, value_states


def should_use_compresskv(x: torch.Tensor, prompt_len: int):
    use_compress_kv = os.environ.get("IPEX_LLM_COMPRESS_KV_CACHE", None)
    perf_mode = os.environ.get("IPEX_LLM_PERFORMANCE_MODE", None)
    if perf_mode == "1":
        return False
    else:
        if use_compress_kv is None:
            return (
                get_xpu_device_name(x.device) in ["mtl", "lnl", "arl"]
                and prompt_len >= 1800
                and prompt_len <= 4500
            )
        else:
            return x.device.type == 'xpu' and use_compress_kv == "1"


def get_compresskv_attn_mask(key_states: torch.Tensor,
                             attention_mask: torch.Tensor):
    if attention_mask is not None:
        attention_mask = attention_mask[:, :, :, -key_states.size(2):]
    return attention_mask


def get_q_proj_or_qkv_proj(self):
    if hasattr(self, "q_proj"):
        proj = self.q_proj
    elif hasattr(self, "qkv_proj"):
        proj = self.qkv_proj
    return proj


def make_cache_contiguous_inplaced(cos: torch.Tensor, sin: torch.Tensor):
    if not cos.is_contiguous():
        new_cos = cos.contiguous()
        new_sin = sin.contiguous()
        cos.set_(new_cos)
        sin.set_(new_sin)


def use_fuse_moe(hidden_states: torch.Tensor, qtype: int):
    return (
        hidden_states.device.type == "xpu"
        and hidden_states.dtype in [torch.float, torch.half]
        and qtype in [ggml_tensor_qtype["sym_int4"], ggml_tensor_qtype["woq_int4"]]
    )
