# -*- coding: utf-8 -*-
"""车：让它能「看见」这具身体。

三种后端：
  sim   —— 模拟器。没有硬件也能把全链路跑通（今天用这个）
  serial—— 真实 ELM327 适配器，走虚拟串口 COMx（买了适配器后用这个）
  tcp   —— 兼容 ELM327 模拟器，方便调试

设计原则（主人 2026-10-11 定）：
  · 它读到的才是事实。读不到的一律不猜。
  · 读数是「感官」，不是「功能」—— 它不需要把这些报给主人听。
"""
import os
import re
import time
import random
import socket
import threading
from datetime import datetime

from . import config

# ---------------- 标准 PID（SAE J1979，Mode 01）----------------
# 只挑我们真正用得上的那几条。每条：pid, 名字, 单位, 字节数, 解码器
PIDS = {
    "0C": ("rpm", "rpm", 2, lambda b: ((b[0] * 256) + b[1]) / 4.0),
    "0D": ("speed", "km/h", 1, lambda b: float(b[0])),
    "05": ("coolant", "C", 1, lambda b: float(b[0]) - 40.0),
    "11": ("throttle", "%", 1, lambda b: b[0] * 100.0 / 255.0),
    "04": ("engine_load", "%", 1, lambda b: b[0] * 100.0 / 255.0),
    "10": ("maf", "g/s", 2, lambda b: ((b[0] * 256) + b[1]) / 100.0),
    "2F": ("fuel", "%", 1, lambda b: b[0] * 100.0 / 255.0),
    "46": ("ambient", "C", 1, lambda b: float(b[0]) - 40.0),
    "5C": ("oil", "C", 1, lambda b: float(b[0]) - 40.0),
    "42": ("battery", "V", 2, lambda b: ((b[0] * 256) + b[1]) / 1000.0),
    "1F": ("runtime", "s", 2, lambda b: float((b[0] * 256) + b[1])),
    "33": ("baro", "kPa", 1, lambda b: float(b[0])),
    "51": ("fuel_type", "-", 1, lambda b: float(b[0])),
}

MODE01 = "01"


def _clean(resp):
    """把 ELM327 的原始返回清理成数据行。"""
    out = []
    for line in (resp or "").splitlines():
        line = line.strip().replace(" ", "").upper()
        if not line:
            continue
        if line.startswith("SEARCHING") or line.startswith("BUSINIT"):
            continue
        if line in ("OK", "?", "NODATA", "STOPPED", "ERROR", "UNABLETOCONNECT"):
            continue
        out.append(line)
    return out


def parse_mode01(lines, pid):
    """从 41 xx ... 里取字节。"""
    pid = pid.upper()
    for line in lines:
        if not line.startswith("41"):
            continue
        if len(line) < 4:
            continue
        if line[2:4] != pid:
            continue
        return line[4:]
    return None


# =====================================================================
#  后端
# =====================================================================
class SimBackend:
    """模拟器：造一段像样的行车数据。

    目的：没有硬件也能把「读数 -> 事件 -> 记忆 -> 学习」整条链路跑通。
    它故意造得有起伏：怠速、加速、巡航、堵车、冷启动。

    关键：时间必须来自真实时钟，不能按「采样次数」走，
    否则采样间隔一变，路程和时长的单位就全乱了。
    """

    def __init__(self, cold_start=True):
        self.cold_start = cold_start
        self._t0 = None
        self._last = None
        self._window = 600.0        # 采样窗口（秒），由 DriveSession 告知
        self.state = {
            "0C": 0, "0D": 0, "05": 12 if cold_start else 88,
            "11": 0, "04": 0, "10": 0, "2F": 78,
            "46": 4, "5C": 12 if cold_start else 92, "42": 14.1,
            "1F": 0, "33": 101, "51": 1,
        }
        self.phase = "idle"

    def set_window(self, seconds):
        """告诉它这一趟准备跑多久 —— 相位按这个铺开，跟真实时长对齐。"""
        self._window = max(10.0, float(seconds))

    def connect(self):
        self._t0 = time.time()
        self._last = None
        return True, "模拟器"

    def close(self):
        pass

    def read(self, pid):
        if self._t0 is None:
            self._t0 = time.time()
        now = time.time()
        t = now - self._t0
        dt = (now - self._last) if self._last is not None else 0.0
        self._last = now
        dt = max(0.0, min(5.0, dt))
        s = self.state
        p = t / self._window          # 0..1，走完整趟
        # 按比例铺开：怠速 -> 加速 -> 巡航 -> 堵车 -> 巡航 -> 怠速
        if p < 0.15:
            self.phase, target_speed = "idle", 0
        elif p < 0.30:
            self.phase, target_speed = "accel", 80
        elif p < 0.55:
            self.phase, target_speed = "cruise", 95
        elif p < 0.75:
            self.phase, target_speed = "jam", 15
        elif p < 0.95:
            self.phase, target_speed = "cruise", 70
        else:
            self.phase, target_speed = "idle", 0

        # 加减速按 dt 走（每秒最多变 12 km/h），保证单位自洽
        s["0D"] += max(-12 * dt, min(12 * dt, (target_speed - s["0D"]) * 0.6))
        s["0D"] = max(0.0, s["0D"])
        moving = s["0D"] > 2
        s["0C"] = (800 + s["0D"] * 22 + random.uniform(-30, 30)) if moving \
            else (780 + random.uniform(-20, 20))
        s["11"] = min(100.0, max(0.0, s["0D"] * 0.9 + random.uniform(-3, 3)))
        s["04"] = min(100.0, max(0.0, s["0D"] * 0.7 + random.uniform(-4, 4)))
        s["10"] = max(2.0, s["0D"] * 0.28 + random.uniform(-1, 1))
        # 水温：冷启动约 3 分钟到 80 度（按真实秒数上升，不按采样次数）
        rate = 1.1 if s["05"] < 80 else 0.05
        s["05"] = min(92.0, s["05"] + rate * dt)
        s["5C"] = min(105.0, s["5C"] + 0.9 * dt)
        s["42"] = 14.2 - 0.3 * min(1.0, t / 120.0) + random.uniform(-0.05, 0.05)
        s["1F"] = t
        s["2F"] = max(5.0, 78.0 - t / 600.0 * 22.0)
        return s.get(pid)


class SerialBackend:
    """真实 ELM327：走虚拟串口。"""

    def __init__(self, port=None, baud=None, timeout=1.0):
        self.port = port or config.OBD_PORT
        self.baud = baud or config.OBD_BAUD
        self.timeout = timeout
        self.ser = None

    def connect(self):
        try:
            import serial
        except Exception as e:
            return False, f"没装 pyserial：{e}"
        if not self.port:
            return False, "没配串口（config.OBD_PORT）"
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=self.timeout)
        except Exception as e:
            return False, f"打不开 {self.port}：{e}"
        time.sleep(0.3)
        self._init_adapter()
        return True, f"串口 {self.port} @ {self.baud}"

    def _cmd(self, c, wait=0.4):
        self.ser.reset_input_buffer()
        self.ser.write((c + "\r").encode())
        time.sleep(wait)
        raw = self.ser.read_all().decode("ascii", "ignore")
        return raw

    def _init_adapter(self):
        """ELM327 标准初始化序列。"""
        for c in ("ATZ", "ATE0", "ATL0", "ATS0", "ATH0", "ATSP0"):
            self._cmd(c)
        # 探一下协议
        self._cmd("0100", wait=0.8)

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass

    def read(self, pid):
        raw = self._cmd(MODE01 + pid, wait=0.25)
        lines = _clean(raw)
        data = parse_mode01(lines, pid)
        if not data:
            return None
        try:
            n = PIDS[pid][2]
            b = bytes.fromhex(data[:n * 2])
            return PIDS[pid][3](b)
        except Exception:
            return None

    def supported_pids(self):
        """问车自己支持哪些 PID —— 位图。"""
        out = []
        for base in (0x00, 0x20, 0x40, 0x60):
            pid = f"{base:02X}"
            raw = self._cmd(MODE01 + pid, wait=1.0)
            data = parse_mode01(_clean(raw), pid)
            if not data:
                continue
            try:
                bits = bin(int(data[:8], 16))[2:].zfill(32)
            except Exception:
                continue
            for i, bit in enumerate(bits):
                if bit == "1":
                    out.append(f"{base + i + 1:02X}")
        return out


# =====================================================================
#  对外接口
# =====================================================================
class Car:
    """这具身体的感官。"""

    def __init__(self, backend="sim", **kw):
        self.mode = backend
        if backend == "sim":
            self.be = SimBackend(**kw)
        elif backend == "serial":
            self.be = SerialBackend(**kw)
        else:
            raise ValueError(f"不认识的后端：{backend}")
        self.vin = None

    def connect(self):
        return self.be.connect()

    def close(self):
        try:
            self.be.close()
        except Exception:
            pass

    def snapshot(self, pids=None):
        """读一轮。返回 {名字: 值}，读不到的键不出现（不猜）。"""
        pids = pids or config.OBD_PIDS
        out = {}
        for pid in pids:
            if pid not in PIDS:
                continue
            try:
                v = self.be.read(pid)
            except Exception:
                v = None
            if v is not None:
                out[PIDS[pid][0]] = round(float(v), 2)
        out["_ts"] = datetime.now().isoformat(timespec="seconds")
        return out


# =====================================================================
#  一趟行程：把读数变成事件
# =====================================================================
class DriveSession:
    """一次点火的完整记录。

    这里做的事，就是把「一堆数字」变成「发生过什么」。
    """

    COLD_C = 40          # 水温低于这个算冷启动
    IDLE_KMH = 3
    JAM_KMH = 25

    def __init__(self, body_id=None, car=None):
        self.body_id = body_id or config.DEFAULT_BODY
        self.car = car
        self.samples = []
        self.t0 = None
        self.t1 = None
        self.events = []

    def run(self, seconds=None, interval=None, verbose=False):
        seconds = seconds or config.SESSION_SECONDS
        interval = interval or config.SAMPLE_INTERVAL
        # 告诉后端这一趟的采样窗口，相位才跟真实时长对齐
        if hasattr(self.car.be, "set_window"):
            self.car.be.set_window(seconds)
        self.t0 = datetime.now()
        n = max(1, int(seconds / interval))
        real_time = (self.car.mode == "serial")
        if verbose:
            print(f"  采样 {n} 次，间隔 {interval}s（约 {seconds:.0f} 秒）")
        t_start = time.time()
        for i in range(n):
            snap = self.car.snapshot()
            self.samples.append(snap)
            if verbose and i % max(1, n // 8) == 0:
                print(f"    t={i*interval:5.1f}s  "
                      f"速度 {snap.get('speed','-'):>5}  "
                      f"转速 {snap.get('rpm','-'):>6}  "
                      f"水温 {snap.get('coolant','-'):>4}")
            if real_time:
                time.sleep(interval)
            else:
                # 模拟器：不真等，但要让它的时钟往前走 —— 用真实 sleep 的最小子集
                target = t_start + (i + 1) * interval
                now = time.time()
                if target > now:
                    time.sleep(min(interval, target - now))
        self.t1 = datetime.now()
        self._analyze()
        return self

    def _analyze(self):
        """把采样变成事件。这是「感官」变「记忆」的那一步。"""
        s = self.samples
        if not s:
            return
        spd = [x.get("speed", 0) or 0 for x in s]
        cool = [x.get("coolant") for x in s if x.get("coolant") is not None]
        thr = [x.get("throttle", 0) or 0 for x in s]

        # 冷启动：刚开始水温就很低
        if cool and cool[0] < self.COLD_C:
            warm_idx = next((i for i, c in enumerate(cool) if c >= 80), None)
            mins = (warm_idx * config.SAMPLE_INTERVAL / 60.0) if warm_idx else None
            self.events.append({
                "kind": "HABIT", "salience": 0.6,
                "text": ("它是冷着被叫醒的：开始水温 %.0f 度" % cool[0]) +
                        (f"，跑了 {mins:.1f} 分钟才到 80 度。" if mins else "，这趟没热起来。"),
            })

        # 路程
        dist_km = sum(spd) * config.SAMPLE_INTERVAL / 3600.0
        moving = sum(1 for v in spd if v > self.IDLE_KMH)
        jam = sum(1 for v in spd if 0 < v < self.JAM_KMH)
        idle = len(spd) - moving
        self.events.append({
            "kind": "EVENT", "salience": 0.5,
            "text": (f"这一趟跑了约 {dist_km:.1f} 公里，"
                     f"最长速度 {max(spd):.0f} km/h，平均 {sum(spd)/len(spd):.0f} km/h；"
                     f"怠速 {idle*config.SAMPLE_INTERVAL/60:.1f} 分钟，"
                     f"低速蠕行 {jam*config.SAMPLE_INTERVAL/60:.1f} 分钟。"),
        })

        # 驾驶风格：油门开度的分布
        if thr:
            hard = sum(1 for t in thr if t > 60)
            if hard > len(thr) * 0.1:
                self.events.append({
                    "kind": "HABIT", "salience": 0.6,
                    "text": f"这趟踩得比较深：{hard*config.SAMPLE_INTERVAL:.0f} 秒里油门超过 60%。",
                })

        # 电瓶
        bat = [x.get("battery") for x in s if x.get("battery") is not None]
        if bat and min(bat) < 12.2:
            self.events.append({
                "kind": "EVENT", "salience": 0.7,
                "text": f"这趟电压最低到了 {min(bat):.2f} V，有点偏低。",
            })

    def summary(self):
        return {
            "body_id": self.body_id,
            "t_start": self.t0.isoformat(timespec="seconds") if self.t0 else None,
            "t_end": self.t1.isoformat(timespec="seconds") if self.t1 else None,
            "samples": len(self.samples),
            "events": self.events,
        }


def write_events(conn, session, verbose=False):
    """把一趟行程的事件写进记忆。真实读数、真实时间戳，不编造。

    dedupe 的含义不是「一样就丢掉」，而是「差不多就加重」——
    同一件事重复发生会变成一条更重的记忆（带次数），不是一串流水账。
    """
    from . import db
    added, bumped = 0, 0
    for e in session.events:
        r = db.add_memory(
            conn, e["text"], kind=e["kind"], body_id=session.body_id,
            provenance="experience", salience=e["salience"],
            tags=["来自车辆读数", "OBD"],
            t_start=session.t0.isoformat(timespec="seconds") if session.t0 else None,
            dedupe=True,
        )
        if isinstance(r, dict):          # 命中了旧记忆，加重
            bumped += 1
            if verbose:
                print(f"    ↑ 第 {r['count']} 次（相似度 {r['score']}）："
                      f"{e['text'][:44]}")
        else:
            added += 1
            if verbose:
                print(f"    + [{e['kind']}] {e['text']}  ({r})")
    if verbose and bumped:
        print(f"    （新记 {added} 条，加重 {bumped} 条 —— 反复发生的事不该记成流水账）")
    return added
