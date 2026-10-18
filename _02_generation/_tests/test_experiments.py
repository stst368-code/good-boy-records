"""Quantities, seeded native controls and caption choices without model weights or paid GPU work."""
from copy import deepcopy
import json
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '_02_generation'), str(ROOT / '_99_tools/_workers_infdit')]
from _gbr_generation.experiments import choices, expand
from _gbr_worker.flow_sampling import flow_step, time_grid, SAMPLERS, SCHEDULES


class ExperimentMathTests(unittest.TestCase):
    def test_decimal_ranges_quantities_and_limits(self):
        self.assertEqual(choices('sampler_cfg', '1.2:2:0.2'), [1.2, 1.4, 1.6, 1.8, 2.0])
        self.assertEqual(choices('sampler_steps', '30:10:-10'), [30, 20, 10])
        cases = expand({'sweeps': {'sampler_cfg': '1,2,3', 'sampler_steps': [10, 20]}, 'repetitions': 2})
        self.assertEqual(len(cases), 12)
        for field, value in [('sampler_cfg', '1:2:0'), ('sampler_steps', '1.1,2'), ('sampler', 'a:b:c'), ('duration', [float('inf')])]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                choices(field, value)
        with self.assertRaisesRegex(ValueError, '10000'):
            expand({'sweeps': {'sampler_steps': '1:1000:1'}, 'repetitions': 11})

    def test_all_flow_solvers_integrate_a_constant_velocity_in_the_correct_direction(self):
        for sampler in SAMPLERS:
            for schedule in SCHEDULES:
                with self.subTest(sampler=sampler, schedule=schedule):
                    grid = time_grid(30, schedule)
                    self.assertEqual((grid[0], grid[-1]), (0, 1))
                    self.assertTrue(all(a < b for a, b in zip(grid, grid[1:])))
                    sample, history = 0., None
                    for t, next_t in zip(grid, grid[1:]):
                        sample, history = flow_step(sample, t, next_t, lambda x, time: 2., sampler, history)
                    self.assertAlmostEqual(sample, 2., places=10)

    def test_higher_order_solvers_approach_the_known_ode_solution(self):
        import math
        errors = {}
        for sampler in ('euler', 'heun', 'midpoint', 'rk4'):
            sample, history = 1., None
            grid = time_grid(20, 'linear')
            for t, end in zip(grid, grid[1:]):
                sample, history = flow_step(sample, t, end, lambda x, time: x, sampler, history)
            errors[sampler] = abs(sample - math.e)
        self.assertLess(errors['heun'], errors['euler'])
        self.assertLess(errors['midpoint'], errors['euler'])
        self.assertLess(errors['rk4'], errors['midpoint'])


@unittest.skipUnless(all(importlib.util.find_spec(name) is not None for name in ('torch', 'diffusers', 'transformers', 'accelerate')), 'Install native dependencies for CPU block checks')
class ConfiguredNativeBlocksTests(unittest.TestCase):
    def test_cfg_and_topk_are_job_local_and_change_real_upstream_depth_sampling(self):
        import torch
        from diffusers.modular_pipelines.minimax_music3 import encoders
        from _gbr_worker.native_blocks import semantic_block
        original = (encoders._AR_CFG_SCALE, encoders._AR_CFG_TOP_K, encoders._AR_SAMPLING_TOP_K)
        def namespace(cfg):
            block = semantic_block({'encoder_cfg': cfg, 'top_k': 1}, torch)
            return block.sub_blocks['generate'].__call__.__wrapped__.__globals__
        scopes = [namespace(0), namespace(2)]
        lm = SimpleNamespace(model=SimpleNamespace(embed_tokens=lambda ids: torch.zeros((*ids.shape, 4))))
        class Depth:
            config = SimpleNamespace(num_codebooks=2, audio_vocab_size=2)
            audio_heads = [lambda hidden: torch.tensor([[10., 0.], [0., 10.]])]
            def projection(self, value): return value
            def __call__(self, value): return value
        values = [scope['_generate_depth_codes'](lm, Depth(), torch.zeros((2, 4)), torch.zeros(2, dtype=torch.long), torch.Generator().manual_seed(4))[0][:, 1].tolist() for scope in scopes]
        self.assertEqual(values, [[1, 1], [0, 0]])
        self.assertEqual((encoders._AR_CFG_SCALE, encoders._AR_CFG_TOP_K, encoders._AR_SAMPLING_TOP_K), original)
        self.assertEqual(scopes[0]['_AR_CFG_SCALE'], 0)

    def test_configured_flow_blocks_use_cfg_zero_one_and_greater_than_one(self):
        import torch
        from _gbr_worker.native_blocks import diffusion_block
        for cfg in (0, .5, 1, 2):
            for sampler in SAMPLERS:
                with self.subTest(cfg=cfg, sampler=sampler):
                    p = dict(sampler=sampler, scheduler='karras', sampler_cfg=cfg)
                    block = diffusion_block(p, torch).sub_blocks['denoise'].sub_blocks['denoise_inner']
                    seen = []
                    def transformer(hidden_states, timestep, encoder_hidden_states, return_dict):
                        seen.append(float(timestep[0]))
                        return (torch.ones_like(hidden_states) * (1 + 2 * float(encoder_hidden_states.mean())),)
                    state = SimpleNamespace(latents=torch.zeros((1, 2, 4)), condition=torch.ones((1, 4, 2)),
                        overlap=0, num_inference_steps=8, progress_bar=SimpleNamespace(update=lambda: None))
                    _, state = block(SimpleNamespace(transformer=transformer), state, 0)
                    self.assertTrue(torch.allclose(state.latents, torch.full_like(state.latents, 1 + 2 * cfg), atol=2e-5))
                    self.assertTrue(all(0 <= value <= 1 for value in seen))

    def test_alternate_solver_keeps_upstream_overlap_conditioning_at_every_evaluation(self):
        import torch
        from _gbr_worker.native_blocks import diffusion_block
        from diffusers.modular_pipelines.minimax_music3.denoise import MiniMaxMusic3ChunkUpdateStep
        p = dict(sampler='heun', scheduler='karras', sampler_cfg=1)
        block = diffusion_block(p, torch).sub_blocks['denoise'].sub_blocks['denoise_inner']
        previous, noise = torch.full((1, 2, 2), 3.), torch.full((1, 2, 2), .5)
        samples = []
        def transformer(hidden_states, timestep, **kwargs):
            time = float(timestep[0])
            samples.append((hidden_states[..., :2].clone(), time))
            return (torch.ones_like(hidden_states),)
        components = SimpleNamespace(transformer=transformer)
        state = SimpleNamespace(latents=torch.zeros((1, 2, 4)), condition=torch.ones((1, 4, 2)),
            previous_latent=previous, noise_prompt=noise, overlap=2, num_inference_steps=4,
            latent_chunks=[], progress_bar=SimpleNamespace(update=lambda: None))
        _, state = block(components, state, 0)
        for sample, time in samples:
            self.assertTrue(torch.allclose(sample, (1 - (1 - 1e-6) * time) * noise + time * previous, atol=1e-6))
        _, state = MiniMaxMusic3ChunkUpdateStep()(components, state, 0)
        self.assertTrue(torch.equal(state.latent_chunks[0][..., :2], previous))


if __name__ == '__main__':
    unittest.main()
