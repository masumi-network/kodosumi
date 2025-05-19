from kodosumi.core import Tracer

def is_prime(n: int) -> bool:
    if n < 2:
        return False
    for i in range(2, int(n ** 0.5) + 1):
        if n % i == 0:
            return False
    return True


def get_prime_gaps_distribution(lb: int, ub: int) -> tuple[int, dict[int, int]]:
    if lb >= ub:
        return 0, {}
    primes = [n for n in range(lb, ub + 1) if is_prime(n)]
    gaps: dict[int, int] = {}
    for i in range(len(primes) - 1):
        gap = primes[i + 1] - primes[i]
        gaps[gap] = gaps.get(gap, 0) + 1
    return len(primes), gaps 

def test_single():
    distribution = get_prime_gaps_distribution(0, 1000000)
    print(distribution)


import ray
from typing import List, Tuple, Any
import datetime


@ray.remote # (num_cpus=4)
def process_range(range_tuple: Tuple[int, int]) -> dict[str, Any]:
    lb, ub = range_tuple
    primes, gaps = get_prime_gaps_distribution(lb, ub)
    if primes > 0:
        for n in range(lb-1, 0, -1):
            if is_prime(n):
                first_prime_in_chunk = min(n for n in range(lb, ub + 1) if is_prime(n))
                gap = first_prime_in_chunk - n
                gaps[gap] = gaps.get(gap, 0) + 1
                break
    return {
        "lower_bound": lb,
        "upper_bound": ub,
        "total_primes": primes,
        "gaps": gaps,
        "processing_node": ray.get_runtime_context().get_node_id(),
        "job_id": ray.get_runtime_context().get_job_id(),
        "timestamp": datetime.datetime.now().isoformat()
    }


def get_prime_gaps_distribution_parallel(lb: int, ub: int, num_tasks: int, tracer: Tracer) -> dict[int, int]:
    if lb >= ub or num_tasks < 1:
        return {}
    range_size = ub - lb + 1
    chunk_size = range_size // num_tasks
    ranges: List[Tuple[int, int]] = []
    
    for i in range(num_tasks):
        start = lb + i * chunk_size
        end = start + chunk_size - 1 if i < num_tasks - 1 else ub
        ranges.append((start, end))
    
    futures = [process_range.remote(range_tuple) for range_tuple in ranges]
    
    final_distribution: dict[int, int] = {}
    remaining_futures = futures.copy()
    completed_jobs = 0
    while remaining_futures:
        done_futures, remaining_futures = ray.wait(remaining_futures, num_returns=1)
        result = ray.get(done_futures[0])
        for gap, count in result["gaps"].items():
            final_distribution[gap] = final_distribution.get(gap, 0) + count
        completed_jobs += 1
        tracer.markdown_sync(f"""
        ###### Job completed ({completed_jobs}/{num_tasks})
        * range: {result['lower_bound']} - {result['upper_bound']}
        * node: {result['processing_node']}
        * found {result['total_primes']} primes

        ```python
        {result['gaps']}
        ```
        <br/>
        """)
        tracer.debug_sync(f"{completed_jobs}/{num_tasks} Jobs completed ({(completed_jobs/num_tasks)*100:.1f}%)")
        # print(f"{completed_jobs}/{num_tasks} Jobs completed ({(completed_jobs/num_tasks)*100:.1f}%)")

    return final_distribution

async def get_prime_gaps_distribution_parallel_async(lb: int, ub: int, num_tasks: int, tracer: Tracer) -> dict[int, int]:
    if lb >= ub or num_tasks < 1:
        return {}
    range_size = ub - lb + 1
    chunk_size = range_size // num_tasks
    ranges: List[Tuple[int, int]] = []
    
    for i in range(num_tasks):
        start = lb + i * chunk_size
        end = start + chunk_size - 1 if i < num_tasks - 1 else ub
        ranges.append((start, end))
    
    futures = [process_range.remote(range_tuple) for range_tuple in ranges]
    
    final_distribution: dict[int, int] = {}
    remaining_futures = futures.copy()
    completed_jobs = 0
    while remaining_futures:
        done_futures, remaining_futures = ray.wait(remaining_futures, num_returns=1)
        result = ray.get(done_futures[0])
        for gap, count in result["gaps"].items():
            final_distribution[gap] = final_distribution.get(gap, 0) + count
        completed_jobs += 1
        await tracer.markdown(f"""
        ###### Job completed ({completed_jobs}/{num_tasks})
        * range: {result['lower_bound']} - {result['upper_bound']}
        * node: {result['processing_node']}
        * found {result['total_primes']} primes

        ```python
        {result['gaps']}
        ```
        <br/>
        """)
        await tracer.debug(f"{completed_jobs}/{num_tasks} Jobs completed ({(completed_jobs/num_tasks)*100:.1f}%)")
        # print(f"{completed_jobs}/{num_tasks} Jobs completed ({(completed_jobs/num_tasks)*100:.1f}%)")

    return final_distribution


def parallel(start, end, tasks):
    t0 = datetime.datetime.now()
    ray.init()
    distribution = get_prime_gaps_distribution_parallel(start, end, tasks)
    print(distribution)
    print("runtime:", datetime.datetime.now() - t0)
    return {
        "distribution": distribution,
        "runtime": datetime.datetime.now() - t0
    }


def test_parallel():
    start = 0 * 20000000
    size = 1 * 10000000
    tasks = 200
    parallel(start, start + size, tasks)


if __name__ == "__main__":
    # test_single()
    test_parallel()