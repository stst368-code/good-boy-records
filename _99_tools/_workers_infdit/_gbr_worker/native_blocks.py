"""Job-local extensions to the pinned Diffusers blocks; never alter installed module globals.

The autoregressive methods are rebound to a private namespace containing this job's CFG/top-k.
Tokenization, feedback embeddings, KV cache and model loading stay in upstream Diffusers.
The alternate flow loop preserves the upstream chunk conditioning and overlap carry.
"""
import functools
import inspect
import types

from .flow_sampling import flow_step, time_grid


def semantic_block(parameters, torch):
    from diffusers.modular_pipelines.minimax_music3 import encoders
    from diffusers.modular_pipelines.minimax_music3.modular_blocks_minimax_music3 import MiniMaxMusic3SemanticGenerationStep
    if parameters['encoder_cfg'] == 1.5 and parameters['top_k'] == 50:
        return MiniMaxMusic3SemanticGenerationStep()
    scope = dict(vars(encoders), _AR_CFG_SCALE=parameters['encoder_cfg'],
                 _AR_CFG_TOP_K=parameters['top_k'], _AR_SAMPLING_TOP_K=parameters['top_k'])

    def bind(function):
        original = inspect.unwrap(function)
        rebound = types.FunctionType(original.__code__, scope, original.__name__, original.__defaults__, original.__closure__)
        rebound.__kwdefaults__ = original.__kwdefaults__
        return functools.update_wrapper(rebound, original)

    for name in ('_sample_top_k', '_generate_depth_codes'):
        scope[name] = bind(getattr(encoders, name))

    class ConfiguredAutoregressive(encoders.MiniMaxMusic3AutoregressiveStep):
        __call__ = torch.no_grad()(bind(encoders.MiniMaxMusic3AutoregressiveStep.__call__))

    class ConfiguredSemantic(MiniMaxMusic3SemanticGenerationStep):
        block_classes = [encoders.MiniMaxMusic3TokenizeStep, ConfiguredAutoregressive]

    return ConfiguredSemantic()


def diffusion_block(parameters, torch):
    from diffusers.modular_pipelines.minimax_music3 import denoise
    from diffusers.modular_pipelines.minimax_music3.modular_blocks_minimax_music3 import MiniMaxMusic3CoreDenoiseStep
    if parameters['sampler'] == 'euler' and parameters['scheduler'] == 'flow_match_euler' and parameters['sampler_cfg'] >= 1:
        return MiniMaxMusic3CoreDenoiseStep()

    class ConfiguredInner(denoise.MiniMaxMusic3ChunkDenoiseInner):
        @torch.no_grad()
        def __call__(self, components, state, k):
            grid = time_grid(state.num_inference_steps, parameters['scheduler'])
            latents, history = state.latents, None
            dtype, device = latents.dtype, latents.device

            def overlap(sample, time):
                if not state.overlap:
                    return sample
                sample = sample.clone()
                sample[..., :state.overlap] = ((1 - (1 - 1e-6) * time) * state.noise_prompt
                                               + time * state.previous_latent[..., :state.overlap])
                return sample

            def velocity(sample, time):
                sample = overlap(sample.to(dtype=dtype), time)
                timestep = torch.full((sample.shape[0],), time, dtype=dtype, device=device)
                cfg = parameters['sampler_cfg']
                def predict(condition):
                    return components.transformer(hidden_states=sample, timestep=timestep,
                        encoder_hidden_states=condition, return_dict=False)[0].float()
                if cfg == 1:
                    return predict(state.condition)
                unconditional = predict(torch.zeros_like(state.condition))
                return unconditional if cfg == 0 else unconditional + cfg * (predict(state.condition) - unconditional)

            for t, next_t in zip(grid, grid[1:]):
                latents = overlap(latents, t)
                latents, history = flow_step(latents.float(), t, next_t, velocity, parameters['sampler'], history)
                latents = latents.to(dtype=dtype)
                state.progress_bar.update()
            state.latents = latents
            return components, state

    class ConfiguredChunks(denoise.MiniMaxMusic3ChunkDenoiseStep):
        block_classes = [denoise.MiniMaxMusic3ChunkConditionStep, denoise.MiniMaxMusic3ChunkPrepareLatentsStep,
                         denoise.MiniMaxMusic3ChunkSetTimestepsStep, ConfiguredInner, denoise.MiniMaxMusic3ChunkUpdateStep]

    class ConfiguredCore(MiniMaxMusic3CoreDenoiseStep):
        block_classes = [list(MiniMaxMusic3CoreDenoiseStep.block_classes)[0], ConfiguredChunks]

    return ConfiguredCore()
