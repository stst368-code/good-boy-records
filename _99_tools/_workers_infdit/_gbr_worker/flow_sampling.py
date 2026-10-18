"""Rectified-flow solvers. MiniMax time increases from noise (0) to audio (1)."""
import math

SAMPLERS = ('euler', 'heun', 'midpoint', 'rk4', 'dpmpp_2m')
SCHEDULES = ('flow_match_euler', 'linear', 'karras', 'exponential', 'cosine')


def time_grid(steps, schedule):
    if type(steps) is not int or steps < 1 or schedule not in SCHEDULES:
        raise ValueError('Invalid flow schedule')
    if schedule in {'linear', 'flow_match_euler'}:
        return [i / steps for i in range(steps + 1)]
    if steps == 1:
        return [0.0, 1.0]
    if schedule == 'cosine':
        return [1 - math.cos(math.pi * i / (2 * steps)) for i in range(steps)] + [1.0]
    # Karras rho=7; exponential and Karras end with a final step from sigma_min=0.001 to zero.
    sigmas = [(1 + i / (steps - 1) * (.001 ** (1/7) - 1)) ** 7 if schedule == 'karras'
              else math.exp(math.log(.001) * i / (steps - 1)) for i in range(steps)]
    return [1 - sigma for sigma in sigmas] + [1.0]


def flow_step(sample, t, next_t, velocity, sampler, history=None):
    """Works with torch tensors or NumPy arrays, without owning RNG or model state."""
    dt = next_t - t
    first = velocity(sample, t)
    if sampler == 'euler':
        return sample + dt * first, None
    if sampler == 'heun':
        if next_t == 1:
            return sample + dt * first, None
        second = velocity(sample + dt * first, next_t)
        return sample + dt * (first + second) / 2, None
    if sampler == 'midpoint':
        second = velocity(sample + .5 * dt * first, t + .5 * dt)
        return sample + dt * second, None
    if sampler == 'rk4':
        second = velocity(sample + .5 * dt * first, t + .5 * dt)
        third = velocity(sample + .5 * dt * second, t + .5 * dt)
        fourth = velocity(sample + dt * third, next_t)
        return sample + dt * (first + 2 * second + 2 * third + fourth) / 6, None
    if sampler == 'dpmpp_2m':
        sigma, next_sigma = 1 - t, 1 - next_t
        denoised = sample + sigma * first
        if next_sigma == 0:
            return denoised, None
        h = math.log(sigma / next_sigma)
        estimate = denoised
        if history is not None:
            old_denoised, old_h = history
            ratio = old_h / h
            estimate = (1 + 1/(2 * ratio)) * denoised - old_denoised / (2 * ratio)
        return next_sigma / sigma * sample - math.expm1(-h) * estimate, (denoised, h)
    raise ValueError('Unsupported flow sampler')
