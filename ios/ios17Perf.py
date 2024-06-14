# -*- coding: utf-8 -*-
"""
sudo python3 iosPerf.pt > data.txt
"""

import dataclasses
import os
import platform
import re
import subprocess
import sys
import threading
import time
import argparse
from datetime import datetime

from ios_device.cli.base import InstrumentsBase
from ios_device.cli.cli import print_json
from ios_device.remote.remote_lockdown import RemoteLockdownClient
from ios_device.util.utils import convertBytes

cpu_data = []
memory_data = []
time_data = []
fps_data = []
jank_data = []
big_jank_data = []

class TunnelManager:
    def __init__(self):
        self.start_event = threading.Event()
        self.tunnel_host = None
        self.tunnel_port = None

    def get_tunnel(self):
        def start_tunnel():
            rp = subprocess.Popen([sys.executable, "-m", "pymobiledevice3", "remote", "start-tunnel"],
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT)
            while not rp.poll():
                try:
                    line = rp.stdout.readline().decode()
                except:
                    print("decode fail {0}".format(line))
                    continue
                line = line.strip()
                if line:
                    print(line)
                if "--rsd" in line:
                    ipv6_pattern = r'--rsd\s+(\S+)\s+'
                    port_pattern = r'\s+(\d{1,5})\b'
                    self.tunnel_host = re.search(ipv6_pattern, line).group(1)
                    self.tunnel_port = int(re.search(port_pattern, line).group(1))
                    self.start_event.set()

        threading.Thread(target=start_tunnel).start()
        self.start_event.wait(timeout=15)


class PerformanceAnalyzer:
    def __init__(self, udid, host, port):
        self.udid = udid
        self.host = host
        self.port = port

    def ios17_proc_perf(self, bundle_id, duration):
        """ Get application performance data """
        start_time = time.time()
        end_time = start_time + duration

        proc_filter = ['Pid', 'Name', 'CPU', 'Memory', 'DiskReads', 'DiskWrites', 'Threads']
        process_attributes = dataclasses.make_dataclass('SystemProcessAttributes', proc_filter)

        while time.time() < end_time:
            def on_callback_proc_message(res):
                if isinstance(res.selector, list):
                    for index, row in enumerate(res.selector):
                        if 'Processes' in row:
                            for _pid, process in row['Processes'].items():
                                attrs = process_attributes(*process)
                                if name and attrs.Name != name:
                                    continue
                                if not attrs.CPU:
                                    attrs.CPU = 0
                                attrs.CPU = f'{round(attrs.CPU, 2)} %'
                                cpu_data.append(attrs.CPU)
                                print(cpu_data)
                                attrs.Memory = convertBytes(attrs.Memory)
                                memory_data.append(attrs.Memory)
                                print(memory_data)
                                attrs.DiskReads = convertBytes(attrs.DiskReads)
                                attrs.DiskWrites = convertBytes(attrs.DiskWrites)
                                print_json(attrs.__dict__, format)


            with RemoteLockdownClient((self.host, self.port)) as rsd:
                with InstrumentsBase(udid=self.udid, network=False, lockdown=rsd) as rpc:
                    rpc.process_attributes = ['pid', 'name', 'cpuUsage', 'physFootprint',
                                              'diskBytesRead', 'diskBytesWritten', 'threadCount']
                    if bundle_id:
                        app = rpc.application_listing(bundle_id)
                        if not app:
                            print(f"not find {bundle_id}")
                            return
                        name = app.get('ExecutableName')
                    rpc.sysmontap(on_callback_proc_message, 1000)

    def ios17_fps_perf(self,duration):
        jank_count = [0]
        big_jank_count = [0]
        frame_times = []
        start_time = time.time()
        end_time = start_time + duration

        while time.time() < end_time:
            def on_callback_fps_message(res):
                data = res.selector
                current_fps = data['CoreAnimationFramesPerSecond']
                now = datetime.now()
                if current_fps == 0:
                    # 避免除以零错误
                    frame_time = float('inf')  # 或者你可以选择其他适当的值，如一个非常大的数字
                else:
                    frame_time = 1 / current_fps * 1000  # 将FPS转换为毫秒的帧时间
                # 跟踪最近的三帧时间
                frame_times.append(frame_time)
                if len(frame_times) > 3:
                    frame_times.pop(0)

                # 检查Jank和BigJank
                if len(frame_times) == 3:
                    avg_frame_time = sum(frame_times) / len(frame_times)
                    movie_frame_time = 1000 / 24 * 2  # 24 FPS视频的两帧时间
                    if frame_time > avg_frame_time * 2 and frame_time > movie_frame_time:
                        jank_count[0] += 1
                        print_json({"currentTime": str(now), "fps": current_fps, "jank": True}, format)
                    if frame_time > avg_frame_time * 2 and frame_time > 1000 / 24 * 3:  # 24 FPS视频的三帧时间
                        big_jank_count[0] += 1
                        print_json({"currentTime": str(now), "fps": current_fps, "bigJank": True}, format)
                fps_data.append(current_fps)
                print(fps_data)
                jank_data.append(jank_count[0])
                print(jank_data)
                big_jank_data.append(big_jank_count[0])
                print(big_jank_data)

                # 输出结果
                print_json(
                    {"currentTime": str(now), "fps": current_fps, "jankCount": jank_count[0], "bigJankCount": big_jank_count[0]},
                    format)

            with RemoteLockdownClient((self.host, self.port)) as rsd:
                with InstrumentsBase(udid=self.udid, network=False, lockdown=rsd) as rpc:
                    rpc.graphics(on_callback_fps_message, 1000)
                    while time.time() < end_time:
                        time.sleep(1)
            return True



if __name__ == '__main__':
    if "Windows" in platform.platform():
        import ctypes
        assert ctypes.windll.shell32.IsUserAnAdmin() == 1, "必须使用管理员权限启动"
    else:
        assert os.geteuid() == 0, "必须使用sudo权限启动"

    parser = argparse.ArgumentParser(description='Performance Analyzer')
    parser.add_argument('--bundle_id',dest='bundle_id', type=str, help='Bundle ID of the application')
    parser.add_argument('--udid', dest='udid', type=str, help='UDID of the device')
    parser.add_argument('--duration', dest='duration', type=int, help='Duration of the test in seconds')

    args = parser.parse_args()
    bundle_id = args.bundle_id
    udid = args.udid
    duration = args.duration

    tunnel_manager = TunnelManager()
    tunnel_manager.get_tunnel()
    performance_analyzer = PerformanceAnalyzer(udid, tunnel_manager.tunnel_host, tunnel_manager.tunnel_port)
    threading.Thread(target=performance_analyzer.ios17_proc_perf, args=(bundle_id,duration)).start()
    time.sleep(0.1)
    threading.Thread(target=performance_analyzer.ios17_fps_perf, args=(duration,)).start()
