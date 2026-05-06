"""Non-blocking LeRobotDataset recorder."""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from lerobot.datasets.lerobot_dataset import LeRobotDataset
from lerobot.utils.constants import ACTION, HF_LEROBOT_HOME, OBS_STR
from lerobot.utils.feature_utils import (
    build_dataset_frame,
    combine_feature_dicts,
    hw_to_dataset_features,
)

logger = logging.getLogger(__name__)


class DatasetRecorder:
    """Wraps a LeRobotDataset with an off-thread save pipeline."""

    def __init__(
        self,
        repo_id: str,
        fps: int,
        observation_features: Mapping[str, Any],
        action_features: Mapping[str, Any],
        task: str,
        *,
        root: str | Path | None = None,
        robot_type: str = "xlerobot",
        num_cameras: int = 3,
        image_writer_threads_per_camera: int = 4,
    ) -> None:
        self._task = task

        obs_ds = hw_to_dataset_features(dict(observation_features), OBS_STR, use_video=True)
        act_ds = hw_to_dataset_features(dict(action_features), ACTION, use_video=True)
        self._ds_features = combine_feature_dicts(obs_ds, act_ds)

        writer_threads = max(1, image_writer_threads_per_camera * max(1, num_cameras))

        os.environ.setdefault("HF_HUB_OFFLINE", "1")

        resolved_root = Path(root) if root is not None else HF_LEROBOT_HOME / repo_id
        tasks_marker = resolved_root / "meta" / "tasks.parquet"
        if tasks_marker.exists():
            logger.info("resuming existing dataset at %s (%s)", resolved_root, repo_id)
            self.dataset = LeRobotDataset.resume(
                repo_id=repo_id,
                root=resolved_root,
                image_writer_processes=0,
                image_writer_threads=writer_threads,
                streaming_encoding=True,
                encoder_threads=2,
            )
        else:
            if resolved_root.exists():
                logger.warning(
                    "dataset root %s has no saved episodes (missing %s); wiping and starting fresh",
                    resolved_root,
                    tasks_marker.relative_to(resolved_root),
                )
                shutil.rmtree(resolved_root)
            logger.info("creating new dataset at %s (%s)", resolved_root, repo_id)
            self.dataset = LeRobotDataset.create(
                repo_id=repo_id,
                fps=fps,
                features=self._ds_features,
                root=root,
                robot_type=robot_type,
                use_videos=True,
                image_writer_processes=0,
                image_writer_threads=writer_threads,
                streaming_encoding=True,
                encoder_threads=2,
            )

        self._recording = False
        self._save_thread: threading.Thread | None = None
        self._save_lock = threading.Lock()
        self._episode_count = 0
        self._pending_errors: list[str] = []
        self._errors_lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def episode_count(self) -> int:
        return self._episode_count

    @property
    def task(self) -> str:
        return self._task

    def start_episode(self, task: str | None = None) -> bool:
        if self._recording:
            return False
        self._await_prior_save()
        if task:
            self._task = task
        self._recording = True
        logger.info("recording started (episode %d, task=%r)", self._episode_count, self._task)
        return True

    def push_frame(self, observation: Mapping[str, Any], action: Mapping[str, Any]) -> None:
        if not self._recording:
            return
        try:
            obs_frame = build_dataset_frame(self._ds_features, dict(observation), prefix=OBS_STR)
            act_frame = build_dataset_frame(self._ds_features, dict(action), prefix=ACTION)
            frame = {**obs_frame, **act_frame, "task": self._task}
            self.dataset.add_frame(frame)
        except Exception as exc:
            logger.exception("push_frame failed")
            self._record_error(f"push_frame: {exc}")
            self._recording = False

    def end_episode(self) -> bool:
        if not self._recording:
            return False
        self._recording = False
        if not self.dataset.has_pending_frames():
            logger.info("end_episode called with zero frames; nothing to save")
            return True
        with self._save_lock:
            self._save_thread = threading.Thread(
                target=self._save_worker,
                name=f"dataset-save-ep{self._episode_count}",
                daemon=True,
            )
            self._save_thread.start()
        self._episode_count += 1
        return True

    def discard_episode(self) -> None:
        self._recording = False
        if not self.dataset.has_pending_frames():
            return
        try:
            self.dataset.clear_episode_buffer(delete_images=True)
        except Exception as exc:
            logger.exception("clear_episode_buffer failed")
            self._record_error(f"discard: {exc}")

    def finalize(self) -> None:
        self._await_prior_save()
        try:
            self.dataset.finalize()
        except Exception as exc:
            logger.exception("dataset.finalize failed")
            self._record_error(f"finalize: {exc}")

    def poll_errors(self) -> list[str]:
        with self._errors_lock:
            errors = self._pending_errors
            self._pending_errors = []
        return errors

    def _save_worker(self) -> None:
        episode = self._episode_count - 1
        started = time.perf_counter()
        try:
            self.dataset.save_episode()
        except Exception as exc:
            logger.exception("save_episode failed for episode %d", episode)
            self._record_error(f"save_episode(ep={episode}): {exc}")
            return
        logger.info("episode %d saved in %.2fs", episode, time.perf_counter() - started)

    def _record_error(self, message: str) -> None:
        with self._errors_lock:
            self._pending_errors.append(message)

    def _await_prior_save(self) -> None:
        with self._save_lock:
            thread = self._save_thread
            self._save_thread = None
        if thread is not None:
            thread.join()
