import { useEffect, useMemo, useRef, useState } from 'react';

type Health = {
  ok?: boolean;
  mock_hardware?: boolean;
  motor_driver?: string;
  ptz_driver?: string;
  lidar_driver?: string;
  imu_driver?: string;
  yahboom_backend?: string;
  yahboom_available?: boolean;
};

type ApiResult = {
  status: number;
  ok: boolean;
  body: unknown;
  elapsedMs: number;
};

type Telemetry = {
  imu?: Record<string, unknown>;
  lidar?: {
    ok?: boolean;
    ranges_m_sample?: Array<number | null>;
    ranges_m?: Array<number | null>;
    angle_min?: number;
    angle_max?: number;
  };
};

const robotApi = (path: string) => `/api/robot${path}`;

function asText(value: unknown) {
  if (typeof value === 'string') return value;
  return JSON.stringify(value, null, 2);
}

async function requestJson(path: string, options?: RequestInit): Promise<ApiResult> {
  const started = performance.now();
  const res = await fetch(robotApi(path), {
    headers: { 'Content-Type': 'application/json', ...(options?.headers || {}) },
    ...options,
  });
  const text = await res.text();
  let body: unknown = text;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  return {
    status: res.status,
    ok: res.ok,
    body,
    elapsedMs: Math.round((performance.now() - started) * 10) / 10,
  };
}

function LidarMiniMap({ lidar }: { lidar?: Telemetry['lidar'] }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#08111f';
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = '#1f3354';
    ctx.lineWidth = 1;
    for (const r of [35, 70, 105]) {
      ctx.beginPath();
      ctx.arc(w / 2, h / 2, r, 0, Math.PI * 2);
      ctx.stroke();
    }
    const ranges = lidar?.ranges_m_sample || lidar?.ranges_m || [];
    if (!ranges.length) {
      ctx.fillStyle = '#7f8aa3';
      ctx.font = '13px system-ui';
      ctx.fillText('라이다 데이터 대기 중...', 54, h / 2);
      return;
    }
    const maxRange = 4;
    ctx.fillStyle = '#4ade80';
    ranges.forEach((value, i) => {
      if (value == null || !Number.isFinite(value)) return;
      const angle = -Math.PI / 2 + (i / Math.max(1, ranges.length - 1)) * Math.PI * 2;
      const radius = Math.min(value / maxRange, 1) * 105;
      const x = w / 2 + Math.cos(angle) * radius;
      const y = h / 2 + Math.sin(angle) * radius;
      ctx.beginPath();
      ctx.arc(x, y, 2.2, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.fillStyle = '#60a5fa';
    ctx.beginPath();
    ctx.arc(w / 2, h / 2, 4, 0, Math.PI * 2);
    ctx.fill();
  }, [lidar]);

  return <canvas className="lidar-canvas" ref={canvasRef} width={280} height={240} />;
}

function imuLine(imu?: Record<string, unknown>) {
  if (!imu) return 'IMU 데이터 대기 중...';
  const acc = (imu.linear_acceleration_mps2 || imu.sample || imu) as Record<string, unknown>;
  return asText(acc).slice(0, 420);
}

export default function App() {
  const [robotId, setRobotId] = useState('robot3');
  const [piIp, setPiIp] = useState('192.168.219.108');
  const [health, setHealth] = useState<Health | null>(null);
  const [apiResult, setApiResult] = useState<ApiResult | null>(null);
  const [telemetry, setTelemetry] = useState<Telemetry | null>(null);
  const [telemetryStatus, setTelemetryStatus] = useState('연결 안 됨');
  const [rtcStatus, setRtcStatus] = useState('대기');
  const [iceStatus, setIceStatus] = useState('대기');
  const [step, setStep] = useState(5);
  const [linear, setLinear] = useState(0.3);
  const [angular, setAngular] = useState(0.5);
  const [log, setLog] = useState<string[]>([]);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const telemetryWsRef = useRef<WebSocket | null>(null);

  const healthText = useMemo(() => {
    if (!health) return '상태를 불러오는 중입니다';
    const drive = `구동: ${health.motor_driver || '-'} / 카메라: ${health.ptz_driver || '-'}`;
    const bridge = `연결: ${health.yahboom_backend || '-'}`;
    const ready = health.yahboom_available ? '로봇 연결 정상' : '로봇 연결 확인 필요';
    return `${ready}\n${drive}\n${bridge}`;
  }, [health]);

  function addLog(message: string) {
    const line = `[${new Date().toLocaleTimeString()}] ${message}`;
    setLog((prev) => [line, ...prev].slice(0, 12));
  }

  async function runApi(path: string, body?: unknown) {
    const result = await requestJson(
      path,
      body == null ? undefined : { method: 'POST', body: JSON.stringify(body) },
    );
    setApiResult(result);
    addLog(`${path} HTTP ${result.status} ${result.elapsedMs}ms`);
    return result;
  }

  async function refreshHealth() {
    const result = await requestJson('/health');
    setApiResult(result);
    setHealth((result.body || {}) as Health);
    addLog(`health HTTP ${result.status} ${result.elapsedMs}ms`);
  }

  useEffect(() => {
    refreshHealth().catch((error) => addLog(String(error)));
    return () => stopWebRtc();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function stopWebRtc() {
    const rid = robotId.trim() || 'robot3';
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'leave', robot_id: rid }));
    }
    wsRef.current?.close();
    wsRef.current = null;
    pcRef.current?.close();
    pcRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setRtcStatus('중지됨');
    setIceStatus('중지됨');
  }

  async function ensurePeerConnection(rid: string) {
    pcRef.current?.close();
    const pc = new RTCPeerConnection({ iceServers: [] });
    pcRef.current = pc;
    pc.onconnectionstatechange = () => setRtcStatus(pc.connectionState);
    pc.oniceconnectionstatechange = () => setIceStatus(pc.iceConnectionState);
    pc.onicecandidate = (event) => {
      if (!event.candidate || wsRef.current?.readyState !== WebSocket.OPEN) return;
      wsRef.current.send(
        JSON.stringify({
          type: 'ice',
          robot_id: rid,
          candidate: {
            candidate: event.candidate.candidate,
            sdpMid: event.candidate.sdpMid,
            sdpMLineIndex: event.candidate.sdpMLineIndex,
          },
        }),
      );
    };
    pc.ontrack = (event) => {
      if (!videoRef.current) return;
      videoRef.current.srcObject = event.streams?.[0] || new MediaStream([event.track]);
    };
    return pc;
  }

  function startWebRtc() {
    stopWebRtc();
    const rid = robotId.trim() || 'robot3';
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${scheme}://${location.host}/ws`);
    wsRef.current = ws;
    setRtcStatus('연결 중');
    ws.onopen = () => {
      addLog(`영상 시그널링 참가: ${rid}`);
      ws.send(JSON.stringify({ type: 'viewer_join', robot_id: rid }));
    };
    ws.onmessage = async (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'offer') {
        const pc = await ensurePeerConnection(rid);
        await pc.setRemoteDescription(new RTCSessionDescription({ type: data.sdp_type || 'offer', sdp: data.sdp }));
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        ws.send(JSON.stringify({ type: 'answer', robot_id: rid, sdp: answer.sdp, sdp_type: 'answer' }));
      } else if (data.type === 'ice' && pcRef.current && data.candidate) {
        await pcRef.current.addIceCandidate(new RTCIceCandidate(data.candidate));
      } else if (data.type === 'error') {
        addLog(`시그널링 오류: ${data.message}`);
      }
    };
    ws.onerror = () => addLog('영상 시그널링 오류');
    ws.onclose = () => setRtcStatus('닫힘');
  }

  function startTelemetry() {
    telemetryWsRef.current?.close();
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${scheme}://${piIp}:8080/ws/telemetry`);
    telemetryWsRef.current = ws;
    setTelemetryStatus('연결 중');
    ws.onopen = () => {
      setTelemetryStatus('연결됨');
      addLog(`센서 웹소켓 연결: ${piIp}`);
    };
    ws.onmessage = (event) => {
      try {
        setTelemetry(JSON.parse(event.data));
      } catch {
        addLog('센서 데이터 JSON 파싱 실패');
      }
    };
    ws.onerror = () => setTelemetryStatus('오류');
    ws.onclose = () => setTelemetryStatus('닫힘');
  }

  function stopTelemetry() {
    telemetryWsRef.current?.close();
    telemetryWsRef.current = null;
    setTelemetryStatus('닫힘');
  }

  return (
    <main className="shell">
      <section className="hero">
        <div>
          <h1>대시보드</h1>
        </div>
        <div className="status-card">
          <span>로봇 상태</span>
          <strong>{healthText}</strong>
          <button onClick={refreshHealth}>새로고침</button>
        </div>
      </section>

      <section className="grid">
        <article className="panel video-panel">
          <div className="panel-head">
            <div>
              <h2>실시간 카메라</h2>
              <p>메인 시그널링 서버를 통한 WebRTC 영상</p>
            </div>
            <div className="row">
              <input value={robotId} onChange={(e) => setRobotId(e.target.value)} />
              <button onClick={startWebRtc}>시작</button>
              <button className="ghost" onClick={stopWebRtc}>중지</button>
            </div>
          </div>
          <video ref={videoRef} autoPlay muted playsInline />
          <div className="metrics">
            <span>피어 연결: {rtcStatus}</span>
            <span>ICE 연결: {iceStatus}</span>
          </div>
        </article>

        <article className="panel">
          <h2>바퀴 제어</h2>
          <div className="control-row">
            <label>
              전후 속도
              <input type="number" step="0.05" value={linear} onChange={(e) => setLinear(Number(e.target.value))} />
            </label>
            <label>
              회전 속도
              <input type="number" step="0.05" value={angular} onChange={(e) => setAngular(Number(e.target.value))} />
            </label>
          </div>
          <div className="pad">
            <span />
            <button onClick={() => runApi('/motors/twist', { linear_m_s: linear, angular_rad_s: 0 })}>앞</button>
            <span />
            <button onClick={() => runApi('/motors/twist', { linear_m_s: 0, angular_rad_s: angular })}>왼쪽</button>
            <button className="danger" onClick={() => runApi('/motors/stop', {})}>정지</button>
            <button onClick={() => runApi('/motors/twist', { linear_m_s: 0, angular_rad_s: -angular })}>오른쪽</button>
            <span />
            <button onClick={() => runApi('/motors/twist', { linear_m_s: -linear, angular_rad_s: 0 })}>뒤</button>
            <span />
          </div>
        </article>

        <article className="panel">
          <h2>카메라 제어</h2>
          <label className="single-input">
            이동 각도
            <input type="number" min="1" max="45" value={step} onChange={(e) => setStep(Number(e.target.value))} />
          </label>
          <div className="pad">
            <span />
            <button onClick={() => runApi('/ptz/delta', { d_pan: 0, d_tilt: step, d_height: 0 })}>위</button>
            <span />
            <button onClick={() => runApi('/ptz/delta', { d_pan: step, d_tilt: 0, d_height: 0 })}>왼쪽</button>
            <button className="ghost" onClick={() => runApi('/ptz/absolute', { pan_deg: 0, tilt_deg: 0, height_mm: null })}>
              중앙
            </button>
            <button onClick={() => runApi('/ptz/delta', { d_pan: -step, d_tilt: 0, d_height: 0 })}>오른쪽</button>
            <span />
            <button onClick={() => runApi('/ptz/delta', { d_pan: 0, d_tilt: -step, d_height: 0 })}>아래</button>
            <span />
          </div>
        </article>

        <article className="panel sensor-panel">
          <div className="panel-head">
            <div>
              <h2>라이다 / IMU</h2>
              <p>파이 웹소켓 센서 데이터</p>
            </div>
            <div className="row">
              <input value={piIp} onChange={(e) => setPiIp(e.target.value)} />
              <button onClick={startTelemetry}>연결</button>
              <button className="ghost" onClick={stopTelemetry}>중지</button>
            </div>
          </div>
          <div className="sensor-grid">
            <LidarMiniMap lidar={telemetry?.lidar} />
            <pre>{imuLine(telemetry?.imu)}</pre>
          </div>
          <div className="metrics">
            <span>센서 연결: {telemetryStatus}</span>
            <span>라이다: {telemetry?.lidar?.ok ? '정상' : '대기'}</span>
          </div>
        </article>

        <article className="panel result-panel">
          <h2>API 응답 / 지연 시간</h2>
          <div className="metrics">
            <span>HTTP: {apiResult?.status ?? '-'}</span>
            <span>브라우저 기준: {apiResult?.elapsedMs ?? '-'}ms</span>
          </div>
          <pre>{apiResult ? asText(apiResult.body) : '아직 API 응답이 없습니다.'}</pre>
        </article>

        <article className="panel">
          <h2>이벤트 로그</h2>
          <pre>{log.join('\n') || '아직 이벤트가 없습니다.'}</pre>
        </article>
      </section>
    </main>
  );
}
