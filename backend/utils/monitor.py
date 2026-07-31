"""
Performance Monitor untuk Face Recognition System
Track CPU, Memory, FPS, dan inference time

Fixed issues:
- CPU sampling was blocking (100ms per call), destroying FPS
- Memory reads were blocking the main loop
- Inference time showed 0 because it was only recorded every 3rd frame
- FPS appeared static due to the blocking CPU call inflating frame time

Solution:
- CPU & memory are sampled in a background thread (non-blocking)
- FPS uses a proper time-window approach for smoother values
- Overlay uses cached stats to avoid any blocking on the render path
"""

import time
import psutil
import threading
from typing import Dict
from collections import deque


class PerformanceMonitor:
    """Monitor performa sistem real-time (non-blocking)"""

    def __init__(self, window_size: int = 60):
        """
        Args:
            window_size: Jumlah sample untuk moving average (default: 60)
        """
        self.window_size = window_size

        # Metrics storage (moving window)
        self.fps_samples = deque(maxlen=window_size)
        self.inference_times = deque(maxlen=window_size)
        self.frame_times = deque(maxlen=window_size)

        # FPS calculation using time-window approach (more stable)
        # Track last 120 frame timestamps
        self._fps_timestamps = deque(maxlen=120)

        # Timestamps
        self.last_frame_time = None
        self.start_time = time.time()

        # Process info
        self.process = psutil.Process()
        self.cpu_count = psutil.cpu_count(logical=True) or 1

        # Stats
        self.total_frames = 0
        self.total_inferences = 0

        # --- Background-sampled metrics (non-blocking) ---
        self._cached_cpu = 0.0
        self._cached_memory = {'rss_mb': 0.0, 'vms_mb': 0.0, 'percent': 0.0}
        self._lock = threading.Lock()
        self._running = True

        # Start background sampling thread
        self._bg_thread = threading.Thread(
            target=self._background_sampler, daemon=True)
        self._bg_thread.start()

        # Kick-start cpu_percent so next call returns meaningful value
        try:
            self.process.cpu_percent(interval=None)
        except Exception:
            pass

    def _background_sampler(self):
        """Background thread that samples CPU and memory every ~1 second.
        This avoids blocking the main rendering loop."""
        while self._running:
            try:
                # cpu_percent(interval=None) returns value since last call —
                # non-blocking
                cpu = self.process.cpu_percent(interval=None)
                mem_info = self.process.memory_info()
                mem = {
                    'rss_mb': mem_info.rss / 1024 / 1024,
                    'vms_mb': mem_info.vms / 1024 / 1024,
                    'percent': self.process.memory_percent()
                }
                with self._lock:
                    self._cached_cpu = cpu
                    self._cached_memory = mem
            except Exception:
                pass

            # Sample every 1 second
            time.sleep(1.0)

    def start_frame(self):
        """Mark start of frame processing"""
        self.last_frame_time = time.perf_counter()

    def end_frame(self):
        """Mark end of frame processing and calculate FPS"""
        now = time.perf_counter()

        if self.last_frame_time is not None:
            frame_time = now - self.last_frame_time
            self.frame_times.append(frame_time)

            # Calculate instantaneous FPS
            if frame_time > 0:
                fps = 1.0 / frame_time
                self.fps_samples.append(fps)

        # Record timestamp for time-window FPS
        self._fps_timestamps.append(now)
        self.total_frames += 1

    def record_inference_time(self, inference_time: float):
        """Record inference time (in seconds)"""
        self.inference_times.append(inference_time)
        self.total_inferences += 1

    def get_cpu_usage(self) -> float:
        """Get cached CPU usage (%) — non-blocking"""
        with self._lock:
            return self._cached_cpu

    def get_app_cpu_percent(self) -> float:
        """Get app CPU normalized to total logical CPU capacity (0-100%)."""
        return min(100.0, self.get_cpu_usage() / self.cpu_count)

    def get_memory_usage(self) -> Dict[str, float]:
        """Get cached memory usage — non-blocking"""
        with self._lock:
            return self._cached_memory.copy()

    def get_fps(self) -> float:
        """Get FPS using time-window approach (more stable than averaging instantaneous FPS)"""
        timestamps = self._fps_timestamps
        if len(timestamps) < 2:
            return 0.0

        # Calculate FPS from frame count over time window
        time_span = timestamps[-1] - timestamps[0]
        if time_span <= 0:
            return 0.0

        return (len(timestamps) - 1) / time_span

    def get_avg_inference_time(self) -> float:
        """Get average inference time (ms)"""
        if len(self.inference_times) == 0:
            return 0.0
        return (sum(self.inference_times) /
                len(self.inference_times)) * 1000  # Convert to ms

    def get_avg_frame_time(self) -> float:
        """Get average frame processing time (ms)"""
        if len(self.frame_times) == 0:
            return 0.0
        return (sum(self.frame_times) / len(self.frame_times)) * \
            1000  # Convert to ms

    def get_uptime(self) -> float:
        """Get uptime in seconds"""
        return time.time() - self.start_time

    def get_overlay_stats(self) -> Dict:
        """Get stats optimized for overlay display — fully non-blocking.
        Uses cached CPU/memory values from the background thread."""
        mem = self.get_memory_usage()

        return {
            'fps': round(self.get_fps(), 1),
            'avg_inference_ms': round(self.get_avg_inference_time(), 1),
            'avg_frame_ms': round(self.get_avg_frame_time(), 1),
            'app_cpu_percent': round(self.get_app_cpu_percent(), 1),
            'cpu_raw_percent': round(self.get_cpu_usage(), 1),
            'memory_mb': round(mem['rss_mb'], 0),
            'memory_percent': round(mem['percent'], 1),
        }

    def get_stats(self) -> Dict:
        """Get all performance stats (non-blocking)"""
        mem = self.get_memory_usage()
        uptime = self.get_uptime()

        return {
            'fps': round(self.get_fps(), 2),
            'avg_inference_ms': round(self.get_avg_inference_time(), 2),
            'avg_frame_ms': round(self.get_avg_frame_time(), 2),
            'app_cpu_percent': round(self.get_app_cpu_percent(), 2),
            'cpu_raw_percent': round(self.get_cpu_usage(), 2),
            'cpu_count': self.cpu_count,
            'memory_mb': round(mem['rss_mb'], 2),
            'memory_percent': round(mem['percent'], 2),
            'total_frames': self.total_frames,
            'total_inferences': self.total_inferences,
            'uptime_seconds': round(uptime, 2),
            'uptime_formatted': self._format_uptime(uptime)
        }

    def _format_uptime(self, seconds: float) -> str:
        """Format uptime as HH:MM:SS"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def print_stats(self):
        """Print current stats to console"""
        stats = self.get_stats()

        print("\n" + "=" * 50)
        print("  PERFORMANCE MONITOR")
        print("=" * 50)
        print(f"FPS:              {stats['fps']:.2f}")
        print(f"Frame Time:       {stats['avg_frame_ms']:.2f} ms")
        print(f"Inference Time:   {stats['avg_inference_ms']:.2f} ms")
        print(f"App CPU Usage:    {stats['app_cpu_percent']:.2f}%")
        print(f"CPU Raw:          {stats['cpu_raw_percent']:.2f}% across {stats['cpu_count']} logical CPUs")
        print(
            f"Memory Usage:     {stats['memory_mb']:.2f} MB ({stats['memory_percent']:.2f}%)")
        print(f"Total Frames:     {stats['total_frames']}")
        print(f"Total Inferences: {stats['total_inferences']}")
        print(f"Uptime:           {stats['uptime_formatted']}")
        print("=" * 50)

    def get_stats_string(self) -> str:
        """Get stats as formatted string for overlay — non-blocking"""
        stats = self.get_overlay_stats()

        lines = [
            f"FPS: {stats['fps']:.1f}",
            f"Frame: {stats['avg_frame_ms']:.1f}ms",
            f"Inference: {stats['avg_inference_ms']:.1f}ms",
            f"App CPU: {stats['app_cpu_percent']:.1f}%",
            f"App RAM: {stats['memory_mb']:.0f}MB"
        ]

        return " | ".join(lines)

    def stop(self):
        """Stop background sampling thread"""
        self._running = False
        if self._bg_thread.is_alive():
            self._bg_thread.join(timeout=2)

    def reset(self):
        """Reset all metrics"""
        self.fps_samples.clear()
        self.inference_times.clear()
        self.frame_times.clear()
        self._fps_timestamps.clear()
        self.total_frames = 0
        self.total_inferences = 0
        self.start_time = time.time()


class PerformanceLogger:
    """Log performance metrics to file"""

    def __init__(self, log_file: str = "logs/performance.log"):
        """
        Args:
            log_file: Path to performance log file
        """
        self.log_file = log_file
        self._ensure_log_dir()

        # Write header
        with open(self.log_file, 'w') as f:
            f.write(
                "timestamp,fps,frame_ms,inference_ms,app_cpu_percent,cpu_raw_percent,memory_mb,memory_percent\n")

    def _ensure_log_dir(self):
        """Ensure log directory exists"""
        import os
        log_dir = os.path.dirname(self.log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)

    def log(self, monitor: PerformanceMonitor):
        """Log current stats"""
        stats = monitor.get_stats()
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        line = f"{timestamp},{stats['fps']},{stats['avg_frame_ms']},{stats['avg_inference_ms']}," \
               f"{stats['app_cpu_percent']},{stats['cpu_raw_percent']},{stats['memory_mb']},{stats['memory_percent']}\n"

        with open(self.log_file, 'a') as f:
            f.write(line)


# Example usage
if __name__ == "__main__":
    pass

    # Create monitor
    monitor = PerformanceMonitor()

    # Simulate processing
    print("Simulating face recognition processing...")
    print("Press Ctrl+C to stop\n")

    try:
        for i in range(100):
            # Start frame
            monitor.start_frame()

            # Simulate inference
            inference_start = time.time()
            time.sleep(0.03)  # Simulate 30ms inference
            inference_time = time.time() - inference_start
            monitor.record_inference_time(inference_time)

            # Simulate frame processing
            time.sleep(0.01)  # Simulate 10ms other processing

            # End frame
            monitor.end_frame()

            # Print stats every 30 frames
            if (i + 1) % 30 == 0:
                monitor.print_stats()

        print("\nFinal stats:")
        monitor.print_stats()

    except KeyboardInterrupt:
        print("\n\nStopped by user")
        monitor.print_stats()
