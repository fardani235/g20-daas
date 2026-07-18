"""File path resolution for task assets"""

import os

DATA_DIR = os.environ.get("DATA_DIR", "/data")


def get_task_path(task_id: str) -> str:
    return os.path.join(DATA_DIR, "tasks", task_id)


def get_raster_path(task_id: str, dataset: str) -> str:
    ext = "tif"
    return os.path.join(get_task_path(task_id), f"{dataset}.{ext}")


def get_pointcloud_path(task_id: str) -> str:
    return os.path.join(get_task_path(task_id), "georeferenced_model.las")
