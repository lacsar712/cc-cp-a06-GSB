import { useCallback, useEffect, useRef, useState } from "preact/hooks";

const TOKEN_KEY = "coldchain_token";
const USER_KEY = "coldchain_user";

function verdictClass(v, status) {
  if (v === "合格") return "tag pass";
  if (v === "超温") return "tag fail";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  if (row.status === "pending") return "待处理";
  if (row.status === "processing") return "处理中";
  return "—";
}

function fmt(v) {
  if (v === null || v === undefined) return "—";
  return Number(v).toString();
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
  const [view, setView] = useState("readings");
  const [loginForm, setLoginForm] = useState({ username: "logger", password: "log123456" });
  const [submitForm, setSubmitForm] = useState({ probe_id: "", temp_c: "" });
  const [biasInput, setBiasInput] = useState("");
  const [rows, setRows] = useState([]);
  const [history, setHistory] = useState(null);
  const [error, setError] = useState("");
  const [biasError, setBiasError] = useState("");
  const [msg, setMsg] = useState("");
  const [biasMsg, setBiasMsg] = useState("");
  const [loading, setLoading] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [correctTemp, setCorrectTemp] = useState("");
  const [rowMsg, setRowMsg] = useState({});
  // 偏置输入框只在首次拿到服务端值时预填一次，避免轮询覆盖正在输入的内容。
  const biasInitRef = useRef(false);

  const isWriter = user?.role === "writer";

  const authHeaders = useCallback(() => {
    const h = { "Content-Type": "application/json" };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }, [token]);

  const loadReadings = useCallback(async () => {
    if (!token) return;
    const res = await fetch("/api/readings", { headers: authHeaders() });
    if (!res.ok) {
      setError("加载列表失败，请重新登录");
      return;
    }
    setRows(await res.json());
  }, [token, authHeaders]);

  const loadHistory = useCallback(async () => {
    if (!token) return;
    const res = await fetch("/api/history", { headers: authHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    setHistory(data);
    if (!biasInitRef.current) {
      biasInitRef.current = true;
      setBiasInput(String(data.bias.bias_c));
    }
  }, [token, authHeaders]);

  useEffect(() => {
    if (!token) return undefined;
    if (view === "readings") loadReadings();
    if (view === "calibration") loadHistory();
    const t = setInterval(() => {
      if (view === "readings") loadReadings();
      if (view === "calibration") loadHistory();
    }, 3000);
    return () => clearInterval(t);
  }, [loadReadings, loadHistory, token, view]);

  async function onLogin(e) {
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
      const data = await res.json();
      localStorage.setItem(TOKEN_KEY, data.access_token);
      localStorage.setItem(
        USER_KEY,
        JSON.stringify({ username: data.username, role: data.role })
      );
      setToken(data.access_token);
      setUser({ username: data.username, role: data.role });
    } finally {
      setLoading(false);
    }
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
    setRows([]);
    setHistory(null);
    setView("readings");
    setBiasInput("");
    biasInitRef.current = false;
  }

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    setLoading(true);
    try {
      const res = await fetch("/api/readings", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          probe_id: submitForm.probe_id,
          temp_c: parseFloat(submitForm.temp_c),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        // 直连与网页共用同一服务端 detail，措辞逐字一致。
        setError(data.detail || "提交失败");
        return;
      }
      setMsg(data.message || "已提交");
      setSubmitForm({ probe_id: "", temp_c: "" });
      await loadReadings();
    } finally {
      setLoading(false);
    }
  }

  async function onSetBias(e) {
    e.preventDefault();
    setBiasError("");
    setBiasMsg("");
    setLoading(true);
    try {
      const res = await fetch("/api/bias", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ bias_c: parseFloat(biasInput) }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        // 越界退回：网页直接展示服务端原文，与直连完全一致。
        setBiasError(data.detail || "偏置设置失败");
        return;
      }
      setBiasMsg(data.message || "偏置已更新");
      setBiasInput(String(data.bias_c));
      await loadHistory();
    } finally {
      setLoading(false);
    }
  }

  async function onCorrect(id) {
    const value = parseFloat(correctTemp);
    if (Number.isNaN(value)) {
      setRowMsg((m) => ({ ...m, [id]: { ok: false, text: "温度必须是数字" } }));
      return;
    }
    setRowMsg((m) => ({ ...m, [id]: null }));
    const res = await fetch(`/api/readings/${id}`, {
      method: "PATCH",
      headers: authHeaders(),
      body: JSON.stringify({ temp_c: value }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setRowMsg((m) => ({ ...m, [id]: { ok: false, text: data.detail || "改正失败" } }));
      return;
    }
    setEditingId(null);
    setCorrectTemp("");
    setRowMsg((m) => ({ ...m, [id]: { ok: true, text: data.message || "已改正（履历旧值未动）" } }));
    await loadReadings();
  }

  if (!token) {
    return (
      <div class="wrap">
        <h1>冷链探头超温台</h1>
        <p class="sub">记录员提交探头编号与摄氏温度，后台工人认领后判定合格或超温。</p>
        <div class="card">
          <form onSubmit={onLogin}>
            <div class="row">
              <label>
                用户名
                <input
                  value={loginForm.username}
                  onInput={(e) =>
                    setLoginForm({ ...loginForm, username: e.target.value })
                  }
                />
              </label>
              <label>
                密码
                <input
                  type="password"
                  value={loginForm.password}
                  onInput={(e) =>
                    setLoginForm({ ...loginForm, password: e.target.value })
                  }
                />
              </label>
              <button type="submit" disabled={loading}>
                登录
              </button>
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

  return (
    <div class="wrap">
      <div class="topbar">
        <div>
          <h1>冷链探头超温台</h1>
          <p class="sub">判定温度 = 原文温度 ＋ 温漂校准偏置；不超过 8℃ 为合格。</p>
        </div>
        <div class="user">
          <nav class="nav">
            <button
              type="button"
              class={view === "readings" ? "navbtn active" : "navbtn"}
              onClick={() => setView("readings")}
            >
              读数台
            </button>
            <button
              type="button"
              class={view === "calibration" ? "navbtn active" : "navbtn"}
              onClick={() => setView("calibration")}
            >
              校准
            </button>
          </nav>
          {user?.username}（{isWriter ? "记录员" : "值班员"}）
          <button type="button" class="secondary" style={{ marginLeft: "0.5rem" }} onClick={logout}>
            退出
          </button>
        </div>
      </div>

      {view === "readings" && (
        <>
          {isWriter && (
            <div class="card">
              <h2 className="h2">提交读数</h2>
              <form onSubmit={onSubmit}>
                <div class="row">
                  <label>
                    探头编号
                    <input
                      required
                      value={submitForm.probe_id}
                      onInput={(e) =>
                        setSubmitForm({ ...submitForm, probe_id: e.target.value })
                      }
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
                      onInput={(e) =>
                        setSubmitForm({ ...submitForm, temp_c: e.target.value })
                      }
                    />
                  </label>
                  <button type="submit" disabled={loading}>
                    提交
                  </button>
                </div>
                {error && <p class="err">{error}</p>}
                {msg && <p class="ok">{msg}</p>}
              </form>
            </div>
          )}

          <div class="card">
            <h2 className="h2">在线单据</h2>
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
                  <tr key={r.id}>
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
                        {editingId === r.id ? (
                          <div class="inline-edit">
                            <input
                              type="number"
                              step="0.1"
                              value={correctTemp}
                              onInput={(e) => setCorrectTemp(e.target.value)}
                              placeholder="改正原文℃"
                            />
                            <button type="button" class="mini" onClick={() => onCorrect(r.id)}>
                              保存
                            </button>
                            <button
                              type="button"
                              class="mini secondary"
                              onClick={() => {
                                setEditingId(null);
                                setCorrectTemp("");
                              }}
                            >
                              取消
                            </button>
                          </div>
                        ) : (
                          <button
                            type="button"
                            class="mini"
                            onClick={() => {
                              setEditingId(r.id);
                              setCorrectTemp(String(r.temp_c));
                              setRowMsg((m) => ({ ...m, [r.id]: null }));
                            }}
                          >
                            改正
                          </button>
                        )}
                        {rowMsg[r.id] && (
                          <p class={rowMsg[r.id].ok ? "ok mini-msg" : "err mini-msg"}>
                            {rowMsg[r.id].text}
                          </p>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colspan={isWriter ? "10" : "9"}>暂无数据</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      {view === "calibration" && (
        <CalibrationView
          isWriter={isWriter}
          history={history}
          biasInput={biasInput}
          setBiasInput={setBiasInput}
          onSetBias={onSetBias}
          loading={loading}
          biasError={biasError}
          biasMsg={biasMsg}
        />
      )}
    </div>
  );
}

function CalibrationView({
  isWriter,
  history,
  biasInput,
  setBiasInput,
  onSetBias,
  loading,
  biasError,
  biasMsg,
}) {
  const bias = history?.bias;
  const biasLedger = history?.bias_ledger ?? [];
  const readingLedger = history?.reading_ledger ?? [];

  return (
    <>
      <div class="card">
        <h2 className="h2">温漂校准偏置</h2>
        <p class="sub" style={{ marginTop: 0 }}>
          当前偏置：<strong>{bias ? fmt(bias.bias_c) : "—"}℃</strong>
          {bias ? `（${bias.updated_by} 于 ${new Date(bias.updated_at).toLocaleString()} 设置）` : ""}
          ，允许闭区间 -10 到 10 摄氏度。提交读数时服务端把原文温度加偏置得到判定温度。
        </p>

        {isWriter ? (
          <form onSubmit={onSetBias}>
            <div class="row">
              <label>
                偏置（℃）
                <input
                  required
                  type="number"
                  step="0.1"
                  value={biasInput}
                  onInput={(e) => setBiasInput(e.target.value)}
                />
              </label>
              <button type="submit" disabled={loading}>
                设置偏置
              </button>
            </div>
            {biasError && <p class="err">{biasError}</p>}
            {biasMsg && <p class="ok">{biasMsg}</p>}
          </form>
        ) : (
          <p class="readonly-note">值班侧只读：可查看偏置与履历，不能修改偏置。</p>
        )}
      </div>

      <div class="card">
        <h2 className="h2">校准履历（偏置改动）</h2>
        <table>
          <thead>
            <tr>
              <th>序号</th>
              <th>旧偏置℃</th>
              <th>新偏置℃</th>
              <th>操作人</th>
              <th>时间</th>
            </tr>
          </thead>
          <tbody>
            {biasLedger.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td>{r.old_bias_c === null ? "—" : fmt(r.old_bias_c)}</td>
                <td>{fmt(r.new_bias_c)}</td>
                <td>{r.updated_by}</td>
                <td>{new Date(r.changed_at).toLocaleString()}</td>
              </tr>
            ))}
            {biasLedger.length === 0 && (
              <tr>
                <td colspan="5">暂无校准记录</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div class="card">
        <h2 className="h2">判定履历（提交时冻结副本）</h2>
        <p class="sub" style={{ marginTop: 0 }}>
          数字为提交瞬间冻结的原文/偏置/判定温度，事后改正单据或偏置均不改旧值，可与在线单据对拍。
        </p>
        <table>
          <thead>
            <tr>
              <th>履历号</th>
              <th>单据号</th>
              <th>探头</th>
              <th>原文℃</th>
              <th>偏置℃</th>
              <th>判定℃</th>
              <th>结论</th>
              <th>状态</th>
              <th>提交人</th>
            </tr>
          </thead>
          <tbody>
            {readingLedger.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td>{r.reading_id}</td>
                <td>{r.probe_id}</td>
                <td>{fmt(r.temp_c)}</td>
                <td>{fmt(r.bias_c)}</td>
                <td>{fmt(r.judged_temp_c)}</td>
                <td>
                  <span class={verdictClass(r.verdict, r.status)}>
                    {displayVerdict(r)}
                  </span>
                </td>
                <td>{r.status}</td>
                <td>{r.created_by}</td>
              </tr>
            ))}
            {readingLedger.length === 0 && (
              <tr>
                <td colspan="9">暂无判定履历</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
