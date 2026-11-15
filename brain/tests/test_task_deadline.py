import asyncio
from types import SimpleNamespace

import pytest

from beacon_brain.loop import Brain


def brain(timeout_s, max_s):
    fake = SimpleNamespace(config=SimpleNamespace(task_timeout_s=timeout_s, task_max_s=max_s),
                           task_deadline=None, task_started=0.0)
    fake.keep_going = lambda: Brain.keep_going(fake)
    return fake


async def run_steps(b, steps, step_s):
    b.task_started = asyncio.get_running_loop().time()
    async with asyncio.timeout(b.config.task_timeout_s) as b.task_deadline:
        for step in range(1, steps + 1):
            if step > 1:
                b.keep_going()
            await asyncio.sleep(step_s)


def test_a_task_that_keeps_making_steps_outlives_one_timeout():
    # 4 steps of 0.1 s = 0.4 s, longer than the 0.25 s idle timeout: each step restarts the clock.
    asyncio.run(run_steps(brain(0.25, 5), 4, 0.1))


def test_one_stuck_step_still_times_out():
    with pytest.raises(TimeoutError):
        asyncio.run(run_steps(brain(0.1, 5), 2, 0.3))


def test_the_whole_task_is_capped():
    with pytest.raises(TimeoutError):
        asyncio.run(run_steps(brain(0.25, 0.3), 6, 0.1))
