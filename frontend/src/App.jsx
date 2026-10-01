import { Fragment } from "preact";
import { useCallback, useEffect, useState } from "preact/hooks";

const TOKEN_KEY = "coldchain_token";
const USER_KEY = "coldchain_user";

function verdictClass(v, status) {
  if (v === "合格") return "tag pass";
  if (v === "超温") return "tag fail";
  if (status === "pending" || status === "processing") return "tag wait";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  if (row.status === "pending") return "待处理";
  if (row.status === "processing") return "处理中";
  return "—";
}

function fmt(n) {
  return n === null || n === undefined ? "—" : String(n);
}

function sameNum(a, b) {
  if (a === null || a === undefined || b === null || b === undefined) return a === b;
  return Math.abs(Number(a) - Number(b)) < 1e-9;
}

async function apiFetch(path, { token, ...opts } = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { ...opts, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    // 一律以服务端原文为准（越界措辞网页与直连一致），前端不另造文案
    const err = new Error(data.detail || `请求失败（${res.status}）`);
    err.status = res.status;
    throw err;
  }
  return data;
}

export function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || "null");
    } catch {
      return null;
    }
  });
  const [view, setView] = useState("desk");

  const isWriter = user?.role === "writer";

  function onLogin(data) {
    localStorage.setItem(TOKEN_KEY, data.access_token);
    localStorage.setItem(
      USER_KEY,
      JSON.stringify({ username: data.username, role: data.role })
    );
    setToken(data.access_token);
    setUser({ username: data.username, role: data.role });
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
  }

  if (!token) {
    return <Login onLogin={onLogin} />;
  }

  return (
    <div class="wrap">
      <div class="topbar">
        <div>
          <h1>冷链探头超温台</h1>
          <p class="sub">原文温度加校准偏置得到判定温度，不超过 8℃ 为合格。</p>
        </div>
        <div class="user">
          {user?.username}（{isWriter ? "记录员" : "值班员"}）
          <button type="button" class="secondary" style={{ marginLeft: "0.5rem" }} onClick={logout}>
            退出
          </button>
        </div>
      </div>

      <div class="nav">
        <button
          type="button"
          class={view === "desk" ? "navbtn active" : "navbtn"}
          onClick={() => setView("desk")}
        >
          超温台
        </button>
        <button
          type="button"
          class={view === "calibration" ? "navbtn active" : "navbtn"}
          onClick={() => setView("calibration")}
        >
          校准落地页
        </button>
      </div>

      {view === "desk" ? (
        <DeskView token={token} isWriter={isWriter} />
      ) : (
        <CalibrationView token={token} isWriter={isWriter} />
      )}
    </div>
  );
}

function Login({ onLogin }) {
  const [loginForm, setLoginForm] = useState({ username: "logger", password: "log123456" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(loginForm),
      });
      if (!res.ok) {
        setError("用户名或密码错误");
        return;
      }
      onLogin(await res.json());
    } finally {
      setLoading(false);
    }
  }

  return (
    <div class="wrap">
      <h1>冷链探头超温台</h1>
      <p class="sub">记录员提交探头编号与摄氏温度，后台工人认领后按判定温度判定合格或超温。</p>
      <div class="card">
        <form onSubmit={onSubmit}>
          <div class="row">
            <label>
              用户名
              <input
                value={loginForm.username}
                onInput={(e) => setLoginForm({ ...loginForm, username: e.target.value })}
              />
            </label>
            <label>
              密码
              <input
                type="password"
                value={loginForm.password}
                onInput={(e) => setLoginForm({ ...loginForm, password: e.target.value })}
              />
            </label>
            <button type="submit" disabled={loading}>登录</button>
          </div>
          {error && <p class="err">{error}</p>}
        </form>
        <p class="sub" style={{ marginBottom: 0 }}>
          记录员 logger / log123456 · 值班员 watcher / watch123456
        </p>
      </div>
    </div>
  );
}

function DeskView({ token, isWriter }) {
  const [submitForm, setSubmitForm] = useState({ probe_id: "", temp_c: "" });
  const [rows, setRows] = useState([]);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);
  const [correctId, setCorrectId] = useState(null);
  const [correctVal, setCorrectVal] = useState("");
  const [correctErr, setCorrectErr] = useState("");

  const loadReadings = useCallback(async () => {
    try {
      setRows(await apiFetch("/api/readings", { token }));
    } catch {
      /* 轮询失败静默，下一轮重试 */
    }
  }, [token]);

  useEffect(() => {
    loadReadings();
    const t = setInterval(loadReadings, 3000);
    return () => clearInterval(t);
  }, [loadReadings]);

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    setLoading(true);
    try {
      const data = await apiFetch("/api/readings", {
        token,
        method: "POST",
        body: JSON.stringify({
          probe_id: submitForm.probe_id,
          temp_c: parseFloat(submitForm.temp_c),
        }),
      });
      setMsg(data.message || "已提交");
      setSubmitForm({ probe_id: "", temp_c: "" });
      await loadReadings();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function submitCorrect(id) {
    setCorrectErr("");
    try {
      await apiFetch(`/api/readings/${id}`, {
        token,
        method: "PATCH",
        body: JSON.stringify({ temp_c: parseFloat(correctVal) }),
      });
      setCorrectId(null);
      setCorrectVal("");
      await loadReadings();
    } catch (err) {
      setCorrectErr(err.message);
    }
  }

  return (
    <>
      {isWriter && (
        <div class="card">
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>提交读数</h2>
          <form onSubmit={onSubmit}>
            <div class="row">
              <label>
                探头编号
                <input
                  required
                  value={submitForm.probe_id}
                  onInput={(e) => setSubmitForm({ ...submitForm, probe_id: e.target.value })}
                  placeholder="例如 探头C03"
                />
              </label>
              <label>
                原文温度（℃）
                <input
                  required
                  type="number"
                  step="0.1"
                  value={submitForm.temp_c}
                  onInput={(e) => setSubmitForm({ ...submitForm, temp_c: e.target.value })}
                />
              </label>
              <button type="submit" disabled={loading}>提交</button>
            </div>
            <p class="hint">判定温度由服务端按「原文温度 + 该探头当前偏置」计算。</p>
            {error && <p class="err">{error}</p>}
            {msg && <p class="ok">{msg}</p>}
          </form>
        </div>
      )}

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>读数列表（在线单据）</h2>
        <table>
          <thead>
            <tr>
              <th>编号</th>
              <th>探头</th>
              <th>原文℃</th>
              <th>偏置℃</th>
              <th>判定℃</th>
              <th>结论</th>
              <th>说明</th>
              <th>状态</th>
              <th>提交人</th>
              {isWriter && <th>操作</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <Fragment key={r.id}>
                <tr>
                  <td>{r.id}</td>
                  <td>{r.probe_id}</td>
                  <td>{fmt(r.temp_c)}</td>
                  <td>{fmt(r.bias_c)}</td>
                  <td>{fmt(r.judged_temp_c)}</td>
                  <td>
                    <span class={verdictClass(r.verdict, r.status)}>
                      {displayVerdict(r)}
                    </span>
                  </td>
                  <td>{r.reason || "—"}</td>
                  <td>{r.status}</td>
                  <td>{r.created_by}</td>
                  {isWriter && (
                    <td>
                      {correctId === r.id ? (
                        <span class="inlinefix">
                          <input
                            type="number"
                            step="0.1"
                            value={correctVal}
                            onInput={(e) => setCorrectVal(e.target.value)}
                            placeholder="新原文℃"
                          />
                          <button type="button" onClick={() => submitCorrect(r.id)}>保存</button>
                          <button
                            type="button"
                            class="secondary"
                            onClick={() => {
                              setCorrectId(null);
                              setCorrectVal("");
                              setCorrectErr("");
                            }}
                          >
                            取消
                          </button>
                        </span>
                      ) : (
                        <button type="button" class="linkbtn" onClick={() => setCorrectId(r.id)}>
                          改正
                        </button>
                      )}
                    </td>
                  )}
                </tr>
                {correctId === r.id && correctErr && (
                  <tr class="fixerr">
                    <td colspan={isWriter ? 10 : 9}>{correctErr}</td>
                  </tr>
                )}
              </Fragment>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colspan={isWriter ? 10 : 9}>暂无数据</td>
              </tr>
            )}
          </tbody>
        </table>
        <p class="hint">改正只改在线单据数字并追加一笔冻结履历，旧履历值不动，可到「校准落地页」对拍。</p>
      </div>
    </>
  );
}

function CalibrationView({ token, isWriter }) {
  const [biasForm, setBiasForm] = useState({ probe_id: "", bias_c: "" });
  const [biases, setBiases] = useState([]);
  const [history, setHistory] = useState([]);
  const [readings, setReadings] = useState([]);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);

  const loadAll = useCallback(async () => {
    try {
      const [b, h, r] = await Promise.all([
        apiFetch("/api/biases", { token }),
        apiFetch("/api/history", { token }),
        apiFetch("/api/readings", { token }),
      ]);
      setBiases(b);
      setHistory(h);
      setReadings(r);
    } catch {
      /* 轮询失败静默，下一轮重试 */
    }
  }, [token]);

  useEffect(() => {
    loadAll();
    const t = setInterval(loadAll, 3000);
    return () => clearInterval(t);
  }, [loadAll]);

  async function onSetBias(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    setLoading(true);
    try {
      // 不在前端做范围拦截：越界由服务端按统一闭区间退回，网页与直连拿到同一句措辞
      const data = await apiFetch("/api/biases", {
        token,
        method: "PUT",
        body: JSON.stringify({
          probe_id: biasForm.probe_id,
          bias_c: parseFloat(biasForm.bias_c),
        }),
      });
      setMsg(data.message || "偏置已保存");
      setBiasForm({ probe_id: "", bias_c: "" });
      await loadAll();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  const histByReading = new Map();
  for (const h of history) {
    if (!histByReading.has(h.reading_id)) histByReading.set(h.reading_id, []);
    histByReading.get(h.reading_id).push(h);
  }

  return (
    <>
      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>偏置设置</h2>
        {isWriter ? (
          <form onSubmit={onSetBias}>
            <div class="row">
              <label>
                探头代号
                <input
                  required
                  value={biasForm.probe_id}
                  onInput={(e) => setBiasForm({ ...biasForm, probe_id: e.target.value })}
                  placeholder="例如 探头C03"
                />
              </label>
              <label>
                校准偏置（℃）
                <input
                  required
                  type="number"
                  step="0.1"
                  value={biasForm.bias_c}
                  onInput={(e) => setBiasForm({ ...biasForm, bias_c: e.target.value })}
                />
              </label>
              <button type="submit" disabled={loading}>提交偏置</button>
            </div>
            <p class="hint">判定用温度 = 原文温度 + 偏置。偏置越出允许闭区间将被服务端退回。</p>
            {error && <p class="err">{error}</p>}
            {msg && <p class="ok">{msg}</p>}
          </form>
        ) : (
          <p class="hint" style={{ marginBottom: 0 }}>
            只读：值班员可查看偏置与履历，不能修改偏置。
          </p>
        )}
      </div>

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>偏置表（当前生效）</h2>
        <table>
          <thead>
            <tr>
              <th>探头代号</th>
              <th>偏置℃</th>
              <th>最近设置人</th>
              <th>设置时间</th>
            </tr>
          </thead>
          <tbody>
            {biases.map((b) => (
              <tr key={b.probe_id}>
                <td>{b.probe_id}</td>
                <td>{fmt(b.bias_c)}</td>
                <td>{b.updated_by}</td>
                <td>{b.updated_at ? new Date(b.updated_at).toLocaleString() : "—"}</td>
              </tr>
            ))}
            {biases.length === 0 && (
              <tr><td colspan="4">尚未配置偏置</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>在线单据 × 冻结副本对拍</h2>
        {readings.map((r) => {
          const copies = histByReading.get(r.id) || [];
          const hasCorrected = copies.some((c) => c.event_type === "corrected");
          const latest = copies.length ? copies[copies.length - 1] : null;
          const matchLatest =
            latest &&
            sameNum(latest.raw_temp_c, r.temp_c) &&
            sameNum(latest.bias_c, r.bias_c) &&
            sameNum(latest.judged_temp_c, r.judged_temp_c) &&
            (latest.verdict || null) === (r.verdict || null);
          return (
            <div class="cmp" key={r.id}>
              <div class="cmphead">
                <strong>读数 #{r.id}</strong> · {r.probe_id}
                {hasCorrected ? (
                  <span class="tag fail">已分叉（在线已改正，旧副本冻结不动）</span>
                ) : (
                  <span class="tag pass">在线与冻结副本一致</span>
                )}
                {latest && (
                  <span class={matchLatest ? "tag pass" : "tag fail"}>
                    {matchLatest ? "对拍一致" : "对拍不符"}
                  </span>
                )}
              </div>
              <table>
                <thead>
                  <tr>
                    <th>来源</th>
                    <th>原文℃</th>
                    <th>偏置℃</th>
                    <th>判定℃</th>
                    <th>结论</th>
                    <th>状态</th>
                    <th>操作人/时间</th>
                  </tr>
                </thead>
                <tbody>
                  <tr class="online">
                    <td>在线单据（当前）</td>
                    <td>{fmt(r.temp_c)}</td>
                    <td>{fmt(r.bias_c)}</td>
                    <td>{fmt(r.judged_temp_c)}</td>
                    <td>{displayVerdict(r)}</td>
                    <td>{r.status}</td>
                    <td>{r.created_by}</td>
                  </tr>
                  {copies.map((c) => (
                    <tr key={c.id}>
                      <td>
                        {c.event_type === "created"
                          ? "冻结·创建"
                          : c.event_type === "judged"
                            ? "冻结·判定"
                            : "冻结·改正"}
                      </td>
                      <td>{fmt(c.raw_temp_c)}</td>
                      <td>{fmt(c.bias_c)}</td>
                      <td>{fmt(c.judged_temp_c)}</td>
                      <td>{c.verdict || "—"}</td>
                      <td>{c.status}</td>
                      <td>
                        {c.operator}
                        {c.created_at ? ` · ${new Date(c.created_at).toLocaleString()}` : ""}
                      </td>
                    </tr>
                  ))}
                  {copies.length === 0 && (
                    <tr><td colspan="7">无冻结副本</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          );
        })}
        {readings.length === 0 && <p class="hint">暂无读数。</p>}
      </div>

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>履历流水（只追加，不改写）</h2>
        <table>
          <thead>
            <tr>
              <th>事件</th>
              <th>读数#</th>
              <th>探头</th>
              <th>原文℃</th>
              <th>偏置℃</th>
              <th>判定℃</th>
              <th>结论</th>
              <th>状态</th>
              <th>操作人</th>
              <th>时间</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h) => (
              <tr key={h.id}>
                <td>
                  <span
                    class={
                      h.event_type === "created"
                        ? "tag wait"
                        : h.event_type === "judged"
                          ? "tag pass"
                          : "tag fail"
                    }
                  >
                    {h.event_type === "created"
                      ? "创建"
                      : h.event_type === "judged"
                        ? "判定"
                        : "改正"}
                  </span>
                </td>
                <td>{h.reading_id}</td>
                <td>{h.probe_id}</td>
                <td>{fmt(h.raw_temp_c)}</td>
                <td>{fmt(h.bias_c)}</td>
                <td>{fmt(h.judged_temp_c)}</td>
                <td>{h.verdict || "—"}</td>
                <td>{h.status}</td>
                <td>{h.operator}</td>
                <td>{h.created_at ? new Date(h.created_at).toLocaleString() : "—"}</td>
              </tr>
            ))}
            {history.length === 0 && (
              <tr><td colspan="10">暂无履历</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
