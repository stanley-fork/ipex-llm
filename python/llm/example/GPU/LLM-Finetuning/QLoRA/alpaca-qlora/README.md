# QLoRA Finetuning with IPEX-LLM

This example ports [Alpaca-LoRA](https://github.com/tloen/alpaca-lora/tree/main) to IPEX-LLM (using [QLoRA](https://arxiv.org/abs/2305.14314) algorithm) on [Intel GPU](../../../README.md).

> Note: You could also refer to [simple QLoRA example](../simple-example/) to try related usage.

### 0. Requirements

To run this example with IPEX-LLM on Intel GPUs, we have some recommended requirements for your machine, please refer to [here](../../../README.md#requirements) for more information.

### 1. Install

For Gemma-2B, directly start from [here](README.md#3-qlora-finetune).

```bash
conda create -n llm python=3.11
conda activate llm
# below command will install intel_extension_for_pytorch==2.1.10+xpu as default
pip install --pre --upgrade ipex-llm[xpu] --extra-index-url https://pytorch-extension.intel.com/release-whl/stable/xpu/us/
pip install transformers==4.36.1 datasets
pip install fire peft==0.10.0 accelerate==0.23.0
pip install oneccl_bind_pt==2.1.100 --extra-index-url https://pytorch-extension.intel.com/release-whl/stable/xpu/us/ # necessary to run distributed finetuning
pip install bitsandbytes scipy
# configures OneAPI environment variables
source /opt/intel/oneapi/setvars.sh # necessary to run before installing deepspeed
pip install git+https://github.com/microsoft/DeepSpeed.git@78c518e
pip install git+https://github.com/intel/intel-extension-for-deepspeed.git@ec33277
# (optional) install mpirun to run multi-card finetuning
sudo apt install openmpi-bin
```

### 2. Configures OneAPI environment variables

```bash
source /opt/intel/oneapi/setvars.sh
```

### 3. QLoRA Finetune

Here, we provide example usages on different hardware. Please refer to the appropriate script based on your device and model:

<details>
  <summary> Show LLaMA2-7B examples </summary>

##### Finetuning LLaMA2-7B on single Arc A770

```bash
bash qlora_finetune_llama2_7b_arc_1_card.sh
```

##### Finetuning LLaMA2-7B on two Arc A770

```bash
bash qlora_finetune_llama2_7b_arc_2_card.sh
```

##### Finetuning LLaMA2-7B on single Data Center GPU Flex 170

```bash
bash qlora_finetune_llama2_7b_flex_170_1_card.sh
```

##### Finetuning LLaMA2-7B on three Data Center GPU Flex 170

```bash
bash qlora_finetune_llama2_7b_flex_170_3_card.sh
```

##### Finetuning LLaMA2-7B on single Intel Data Center GPU Max 1100

```bash
bash qlora_finetune_llama2_7b_pvc_1100_1_card.sh
```

##### Finetuning LLaMA2-7B on four Intel Data Center GPU Max 1100

```bash
bash qlora_finetune_llama2_7b_pvc_1100_4_card.sh
```

##### Finetuning LLaMA2-7B on single Intel Data Center GPU Max 1550

```bash
bash qlora_finetune_llama2_7b_pvc_1550_1_card.sh
```

##### Finetuning LLaMA2-7B on four Intel Data Center GPU Max 1550

```bash
bash qlora_finetune_llama2_7b_pvc_1550_4_card.sh
```

</details>

<details>
  <summary> Show LLaMA2-13B examples </summary>

##### Finetuning LLaMA2-13B on single tile of Intel Data Center GPU Max 1550

```bash
bash qlora_finetune_llama2_13b_pvc_1550_1_tile.sh
```

##### Finetuning LLaMA2-13B on single Intel Data Center GPU Max 1550

```bash
bash qlora_finetune_llama2_13b_pvc_1550_1_card.sh
```

##### Finetuning LLaMA2-13B on four Intel Data Center GPU Max 1550

```bash
bash qlora_finetune_llama2_13b_pvc_1550_4_card.sh
```

</details>

<details>
  <summary> Show LLaMA2-70B examples </summary>

Different from `LLaMA2-7B` and `LLaMA2-13B`, it is recommonded to save the model with ipex-llm low-bit optimization first to avoid large amount of CPU memory usage. And DeepSpeed ZeRO2 technology is used during finetuning.

##### Finetuning LLaMA2-70B on one Intel Data Center GPU Max 1550

```bash
bash qlora_finetune_llama2_70b_pvc_1550_1_card.sh
```

##### Finetuning LLaMA2-70B on four Intel Data Center GPU Max 1550

```bash
bash qlora_finetune_llama2_70b_pvc_1550_4_card.sh
```

</details>

<details>
  <summary> Show LLaMA3-8B examples </summary>

##### Finetuning LLaMA3-8B on single Arc A770

```bash
bash qlora_finetune_llama3_8b_arc_1_card.sh
```

</details>

<details>
  <summary> Show ChatGLM3-6B examples </summary>

##### Finetuning ChatGLM3-6B examples on single Arc A770

```bash
bash qlora_finetune_chatglm3_6b_arc_1_card.sh
```

</details>

<details>
  <summary> Show Qwen-1.5-7B examples </summary>

##### Finetuning Qwen-1.5-7B examples on single Arc A770

Install transformers 4.37.0

```bash
pip install transformers==4.37.0
```

```bash
bash qlora_finetune_qwen15_7b_arc_1_card.sh
```

</details>

<details>
  <summary> Show Gemma-2B example </summary>

##### 1. Install

```bash
conda create -n llm python=3.11
conda activate llm
# below command will install intel_extension_for_pytorch==2.1.10+xpu as default
pip install --pre --upgrade ipex-llm[xpu] --extra-index-url https://pytorch-extension.intel.com/release-whl/stable/xpu/us/
pip install datasets
pip install transformers==4.38.1
pip install accelerate==0.27.2
pip install bitsandbytes==0.45.3
```
##### 2. Configures OneAPI environment variables

```bash
source /opt/intel/oneapi/setvars.sh
```

##### 3. Run on A770

```bash
bash qlora_finetune_gemma_2b_arc_1_card.sh
```

</details>

<details>
  <summary> Show Baichuan2-7B examples </summary>

##### Finetuning Baichuan2-7B examples on single Arc A770

Please download [Baichuan2-7B-Chat](https://huggingface.co/baichuan-inc/Baichuan2-7B-Chat). Modify `modeling_baichuan.py` in model dir. Add following 2 lines into Line 234. This change fixes [Baichuan2 type mismatch issue](https://github.com/baichuan-inc/Baichuan2/issues/291).

```python
if(attention_mask.dtype != query_states.dtype):
    attention_mask = attention_mask.to(query_states.dtype)
```

After modification, line 234-236 should look like below.

```python
with torch.backends.cuda.sdp_kernel(enable_flash=True, enable_math=True, enable_mem_efficient=True):
    if(attention_mask.dtype != query_states.dtype):
        attention_mask = attention_mask.to(query_states.dtype)
    attn_output = F.scaled_dot_product_attention(query_states, key_states, value_states, attn_mask = attention_mask)
```
Modify `--base_model` in `qlora_finetune_baichuan2_7b_arc_1_card.sh`. Then, launch finetune.

```bash
bash qlora_finetune_baichuan2_7b_arc_1_card.sh
```

</details>

### 4. (Optional) Resume Training

If you fail to complete the whole finetuning process, it is suggested to resume training from a previously saved checkpoint by specifying `resume_from_checkpoint` to the local checkpoint folder as following:**

```bash
python ./alpaca_qlora_finetuning.py \
    --base_model "meta-llama/Llama-2-7b-hf" \
    --data_path "yahma/alpaca-cleaned" \
    --output_dir "./ipex-llm-qlora-alpaca" \
    --resume_from_checkpoint "./ipex-llm-qlora-alpaca/checkpoint-1100"
```

### 5. Sample Output

```log
{'loss': 1.9231, 'learning_rate': 2.9999945367033285e-05, 'epoch': 0.0}                                                                                                                          
{'loss': 1.8622, 'learning_rate': 2.9999781468531096e-05, 'epoch': 0.01}                                                                                                                         
{'loss': 1.9043, 'learning_rate': 2.9999508305687345e-05, 'epoch': 0.01}                                                                                                                         
{'loss': 1.8967, 'learning_rate': 2.999912588049185e-05, 'epoch': 0.01}                                                                                                                          
{'loss': 1.9658, 'learning_rate': 2.9998634195730358e-05, 'epoch': 0.01}                                                                                                                         
{'loss': 1.8386, 'learning_rate': 2.9998033254984483e-05, 'epoch': 0.02}                                                                                                                         
{'loss': 1.809, 'learning_rate': 2.999732306263172e-05, 'epoch': 0.02}                                                                                                                           
{'loss': 1.8552, 'learning_rate': 2.9996503623845395e-05, 'epoch': 0.02}                                                                                                                         
  1%|█                                                                                                                                                         | 8/1164 [xx:xx<xx:xx:xx, xx s/it]
```

### 6. Merge the adapter into the original model

```
python ./export_merged_model.py --repo-id-or-model-path REPO_ID_OR_MODEL_PATH --adapter_path ./outputs/checkpoint-200 --output_path ./outputs/checkpoint-200-merged
```

Then you can use `./outputs/checkpoint-200-merged` as a normal huggingface transformer model to do inference.

### 7. Troubleshooting

Please refer to [here](../../README.md#troubleshooting) for solutions of common issues during finetuning.
