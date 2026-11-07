from __future__ import annotations

import heapq


def select_mesh_build_jobs(jobs, limit, chunks):
    limit = max(0, int(limit))
    if limit == 0 or not jobs:
        return []

    def priority(job):
        chunk = chunks.get(job[5])
        has_mesh = bool(getattr(chunk, "_has_mesh", False)) if chunk else False
        return (job[0], 0 if not has_mesh else 1,
                job[1], job[2], job[3], job[4])

    target = min(limit, len(jobs))
    near_available = sum(1 for job in jobs if job[0] <= 1)
    far_available = len(jobs) - near_available

    near_slots = (target + 1) // 2
    far_slots = target // 2
    if near_available < near_slots:
        far_slots = min(far_available, far_slots + near_slots - near_available)
    if far_available < far_slots:
        near_slots = min(near_available, near_slots + far_slots - far_available)

    near_jobs = heapq.nsmallest(
        near_slots, (job for job in jobs if job[0] <= 1), key=priority,
    )
    far_jobs = heapq.nsmallest(
        far_slots, (job for job in jobs if job[0] > 1), key=priority,
    )

    selected = []
    for index in range(max(len(near_jobs), len(far_jobs))):
        if index < len(near_jobs):
            selected.append(near_jobs[index])
        if index < len(far_jobs):
            selected.append(far_jobs[index])
    return selected
