---
tab: Tech
title: The tools and tech used
order: 3
---

> These tools describe earlier published recordings. The current GBR runner uses the native MiniMax diffusion/Transformers pipeline; historical recording metadata is retained.

Below is an overview of the tools, models and technologies used to build the
workflow, design the music, set up this website and construct its contents.

### Languages

- [Python](https://www.python.org/) : it's the basis of most AI/ML coding and one of the top choices for data analytics pipelines

### Software

- [PyCharm](https://www.jetbrains.com/pycharm/) : my go-to Python IDE

- [ComfyUI](https://github.com/comfy-org/comfyui) : the lego bricks type interface I used to build most of the tracks found here. Very easy to see the whole workflow and fine tune parameters to get the best results.
    - **Custom Nodes**
        - [KJ Nodes](https://github.com/kijai/ComfyUI-KJNodes)

- [Notepad++](https://notepad-plus-plus.org/) : working with .yaml/.json, quick .py edits

### AI

- [ACE-Step 1.5](https://github.com/ace-step/ACE-Step-1.5)

- [MiniMax Music 3](https://github.com/MiniMax-AI/MiniMax-Music3) : very caption heavy model, my preferred choice, details on the following models can be found over on the workflow tab!
    - **Text Encoders**
        - [minimax_music3_text_encoder_bf16.safetensors](https://huggingface.co/Comfy-Org/MiniMax-Music-3/blob/main/text_encoders/minimax_music3_text_encoder_bf16.safetensors)
        - [minimax_music3_text_encoder_pruned_bf16.safetensors](https://huggingface.co/Comfy-Org/MiniMax-Music-3/blob/main/text_encoders/minimax_music3_text_encoder_pruned_bf16.safetensors)
        - [minimax_music3_text_encoder_pruned_int8_convrot.safetensors](https://huggingface.co/Comfy-Org/MiniMax-Music-3/blob/main/text_encoders/minimax_music3_text_encoder_pruned_int8_convrot.safetensors)
    - **Diffusion Transformers**
        - [minimax_music3_dit_fp16.safetensors](https://huggingface.co/Comfy-Org/MiniMax-Music-3/blob/main/diffusion_models/minimax_music3_dit_fp16.safetensors)
        - [minimax_music3_dit_fp32.safetensors](https://huggingface.co/Comfy-Org/MiniMax-Music-3/blob/main/diffusion_models/minimax_music3_dit_fp32.safetensors)
        - [minimax_music3_dit_int8_convrot.safetensors](https://huggingface.co/Comfy-Org/MiniMax-Music-3/blob/main/diffusion_models/minimax_music3_dit_int8_convrot.safetensors)
    - **VAE**
        - [minimax_music3_dav.safetensors](https://huggingface.co/Comfy-Org/MiniMax-Music-3/blob/main/vae/minimax_music3_dav.safetensors)

- [Demucs](https://github.com/adefossez/demucs) : splits audio tracks into vocals and instrumentation, the first stage of automating the time-sync of lyrics

- [WhisperX](https://github.com/m-bain/whisperx) : uses vocal track and lyrics file to timecode ( to .000 position ) each word spoken on the track, enabling the per word highlighting you see on the page here.
    - [faster-whisper-large-v3](https://huggingface.co/Systran/faster-whisper-large-v3)

### Services

- [RunPod](https://www.runpod.io/) : offload compute or short term rental of server grade compute. Enables loading models not possible on my home computer, or parallelising workloads by running multiple pods at once

- [GitHub](https://github.com/) (you're here!) : version control and free web-hosting for this page

### Data storage formats

- `.yaml`
- `.json`
- `.md`
