import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AlertCircle,
  ArrowDownLeft,
  ArrowUpRight,
  Banknote,
  Clock3,
  CreditCard,
  Eye,
  EyeOff,
  Loader2,
  LogOut,
  RefreshCcw,
  Send,
  ShieldCheck,
  UserPlus,
  Wallet,
} from "lucide-react";
import "./styles.css";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const ACCESS_TOKEN_KEY = "playto_pay_access_token";
const REFRESH_TOKEN_KEY = "playto_pay_refresh_token";

function getToken() {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

function normalizeTokens(data) {
  return {
    access: data?.access ?? data?.access_token ?? data?.tokens?.access ?? data?.tokens?.access_token,
    refresh: data?.refresh ?? data?.refresh_token ?? data?.tokens?.refresh ?? data?.tokens?.refresh_token,
  };
}

function saveTokens(tokens) {
  const normalized = normalizeTokens(tokens);
  if (normalized.access) {
    localStorage.setItem(ACCESS_TOKEN_KEY, normalized.access);
  }
  if (normalized.refresh) {
    localStorage.setItem(REFRESH_TOKEN_KEY, normalized.refresh);
  }
  return normalized;
}

function clearTokens() {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}

function getRefreshToken() {
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

function makeIdempotencyKey() {
  if (crypto?.randomUUID) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function extractErrorMessage(data, fallback) {
  if (!data) return fallback;
  if (data.detail || data.error || data.message) {
    return data.detail || data.error || data.message;
  }
  const fieldMessages = Object.entries(data)
    .map(([field, value]) => {
      const message = Array.isArray(value) ? value.join(" ") : String(value);
      return `${field}: ${message}`;
    })
    .join(" ");
  return fieldMessages || fallback;
}

async function refreshAccessToken() {
  const refresh = getRefreshToken();
  if (!refresh) return false;

  const response = await fetch(`${API_BASE_URL}/api/v1/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh }),
  });
  if (!response.ok) return false;
  const data = await response.json();
  saveTokens({ ...data, refresh });
  return Boolean(data?.access);
}

async function apiFetch(path, options = {}, retryOnUnauthorized = true) {
  const token = getToken();
  const headers = new Headers(options.headers ?? {});

  if (!headers.has("Content-Type") && options.body) {
    headers.set("Content-Type", "application/json");
  }
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  });

  let data = null;
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    data = await response.json();
  }

  if (!response.ok) {
    if (response.status === 401 && retryOnUnauthorized && path !== "/api/v1/auth/refresh") {
      const refreshed = await refreshAccessToken();
      if (refreshed) {
        return apiFetch(path, options, false);
      }
    }
    const message = extractErrorMessage(data, `Request failed with status ${response.status}`);
    const error = new Error(message);
    error.status = response.status;
    error.data = data;
    throw error;
  }

  return data;
}

const api = {
  login: (payload) =>
    apiFetch("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  register: (payload) =>
    apiFetch("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  me: () => apiFetch("/api/v1/me"),
  balance: () => apiFetch("/api/v1/balance"),
  ledger: () => apiFetch("/api/v1/ledger"),
  payouts: () => apiFetch("/api/v1/payouts"),
  createPayout: (payload) =>
    apiFetch("/api/v1/payouts", {
      method: "POST",
      headers: {
        "Idempotency-Key": makeIdempotencyKey(),
      },
      body: JSON.stringify(payload),
    }),
};

function asArray(value) {
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.results)) return value.results;
  if (Array.isArray(value?.items)) return value.items;
  if (Array.isArray(value?.data)) return value.data;
  return [];
}

function formatMoney(paise = 0) {
  const rupees = Number(paise || 0) / 100;
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
  }).format(rupees);
}

function formatDate(value) {
  if (!value) return "Not available";
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function StatusPill({ status }) {
  const normalized = String(status ?? "unknown").toLowerCase();
  const styles = {
    completed: "border-emerald-200 bg-emerald-50 text-emerald-700",
    processing: "border-sky-200 bg-sky-50 text-sky-700",
    pending: "border-amber-200 bg-amber-50 text-amber-700",
    failed: "border-rose-200 bg-rose-50 text-rose-700",
    reversed: "border-slate-200 bg-slate-100 text-slate-700",
    settled: "border-emerald-200 bg-emerald-50 text-emerald-700",
    held: "border-amber-200 bg-amber-50 text-amber-700",
  };

  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium ${styles[normalized] ?? "border-slate-200 bg-slate-50 text-slate-600"}`}>
      {normalized}
    </span>
  );
}

function AuthScreen({ onAuthenticated }) {
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({
    email: "",
    password: "",
    name: "",
    merchant_name: "",
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    setLoading(true);

    try {
      const payload =
        mode === "register"
          ? {
              email: form.email,
              password: form.password,
              name: form.name,
              merchant_name: form.merchant_name || form.name || form.email,
            }
          : { email: form.email, password: form.password };

      const data = mode === "register" ? await api.register(payload) : await api.login(payload);
      const tokens = saveTokens(data);

      if (!tokens.access) {
        throw new Error("Authentication response did not include an access token.");
      }

      onAuthenticated();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-slate-100">
      <section className="mx-auto grid min-h-screen max-w-6xl items-center gap-10 px-5 py-10 lg:grid-cols-[1fr_420px]">
        <div className="max-w-2xl">
          <div className="mb-8 inline-flex h-12 w-12 items-center justify-center rounded-lg bg-slate-950 text-white">
            <Wallet size={24} />
          </div>
          <h1 className="text-4xl font-semibold tracking-normal text-slate-950 sm:text-5xl">
            Playto Pay merchant payouts
          </h1>
          <p className="mt-5 max-w-xl text-lg leading-8 text-slate-600">
            A focused dashboard for balance visibility, payout requests, live payout status, and ledger history.
          </p>
          <div className="mt-8 grid max-w-2xl gap-4 sm:grid-cols-3">
            <Feature icon={ShieldCheck} label="JWT secured" />
            <Feature icon={RefreshCcw} label="Live polling" />
            <Feature icon={Banknote} label="Paise-first money" />
          </div>
        </div>

        <form onSubmit={submit} className="rounded-lg border border-slate-200 bg-white p-6 shadow-panel">
          <div className="mb-6 flex items-center justify-between gap-4">
            <div>
              <h2 className="text-xl font-semibold text-slate-950">
                {mode === "login" ? "Sign in" : "Create account"}
              </h2>
              <p className="mt-1 text-sm text-slate-500">Use seeded credentials or register if enabled.</p>
            </div>
            <button
              type="button"
              onClick={() => {
                setMode(mode === "login" ? "register" : "login");
                setError("");
              }}
              className="inline-flex h-10 items-center gap-2 rounded-md border border-slate-200 px-3 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              <UserPlus size={16} />
              {mode === "login" ? "Register" : "Login"}
            </button>
          </div>

          {mode === "register" && (
            <>
              <TextField
                label="Name"
                value={form.name}
                onChange={(value) => setForm((current) => ({ ...current, name: value }))}
                autoComplete="name"
              />
              <TextField
                label="Merchant name"
                value={form.merchant_name}
                onChange={(value) => setForm((current) => ({ ...current, merchant_name: value }))}
                autoComplete="organization"
              />
            </>
          )}

          <TextField
            label="Email"
            type="email"
            value={form.email}
            onChange={(value) => setForm((current) => ({ ...current, email: value }))}
            autoComplete="email"
            required
          />
          <TextField
            label="Password"
            type="password"
            value={form.password}
            onChange={(value) => setForm((current) => ({ ...current, password: value }))}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            required
          />

          {error && <InlineError message={error} />}

          <button
            type="submit"
            disabled={loading}
            className="mt-4 inline-flex h-11 w-full items-center justify-center gap-2 rounded-md bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-70"
          >
            {loading ? <Loader2 className="animate-spin" size={18} /> : <ShieldCheck size={18} />}
            {mode === "login" ? "Sign in" : "Create and sign in"}
          </button>
        </form>
      </section>
    </main>
  );
}

function Feature({ icon: Icon, label }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white p-4 text-sm font-medium text-slate-700 shadow-sm">
      <Icon size={18} className="text-slate-500" />
      {label}
    </div>
  );
}

function TextField({ label, value, onChange, type = "text", required = false, autoComplete }) {
  const [isPasswordVisible, setIsPasswordVisible] = useState(false);
  const canReveal = type === "password";
  const inputType = canReveal && isPasswordVisible ? "text" : type;
  const PasswordIcon = isPasswordVisible ? EyeOff : Eye;

  return (
    <label className="mb-4 block">
      <span className="mb-1.5 block text-sm font-medium text-slate-700">{label}</span>
      <div className="relative">
        <input
          type={inputType}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          required={required}
          autoComplete={autoComplete}
          className={`h-11 w-full rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-950 outline-none transition focus:border-slate-500 focus:ring-4 focus:ring-slate-200 ${
            canReveal ? "pr-12" : ""
          }`}
        />
        {canReveal && (
          <button
            type="button"
            aria-label={isPasswordVisible ? "Hide password" : "Show password"}
            title={isPasswordVisible ? "Hide password" : "Show password"}
            onClick={() => setIsPasswordVisible((current) => !current)}
            className="absolute right-1.5 top-1/2 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-800 focus:outline-none focus:ring-2 focus:ring-slate-300"
          >
            <PasswordIcon size={18} />
          </button>
        )}
      </div>
    </label>
  );
}

function InlineError({ message }) {
  return (
    <div className="mt-3 flex items-start gap-2 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
      <AlertCircle className="mt-0.5 shrink-0" size={16} />
      <span>{message}</span>
    </div>
  );
}

function Dashboard({ onLogout }) {
  const [me, setMe] = useState(null);
  const [balance, setBalance] = useState(null);
  const [ledger, setLedger] = useState([]);
  const [payouts, setPayouts] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState(null);
  const requestIdRef = useRef(0);

  const loadDashboard = async ({ initial = false } = {}) => {
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    if (initial) setLoading(true);
    setError("");

    try {
      const [meData, balanceData, ledgerData, payoutsData] = await Promise.all([
        api.me(),
        api.balance(),
        api.ledger(),
        api.payouts(),
      ]);
      if (requestId === requestIdRef.current) {
        setMe(meData);
        setBalance(balanceData);
        setLedger(asArray(ledgerData));
        setPayouts(asArray(payoutsData));
        setLastUpdated(new Date());
      }
    } catch (err) {
      if (err.status === 401) {
        clearTokens();
        onLogout();
        return;
      }
      if (requestId === requestIdRef.current) {
        setError(err.message);
      }
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    loadDashboard({ initial: true });
    const intervalId = window.setInterval(() => loadDashboard(), 3000);
    return () => window.clearInterval(intervalId);
  }, []);

  const merchantLabel =
    me?.merchant_name || me?.merchant?.name || me?.name || me?.email || "Merchant";

  return (
    <main className="min-h-screen bg-slate-100 text-slate-950">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-5 py-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-slate-950 text-white">
              <Wallet size={22} />
            </div>
            <div>
              <p className="text-sm font-medium text-slate-500">Playto Pay</p>
              <h1 className="text-xl font-semibold text-slate-950">{merchantLabel}</h1>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="hidden items-center gap-2 text-sm text-slate-500 sm:flex">
              <Clock3 size={16} />
              {lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : "Loading"}
            </div>
            <IconButton label="Refresh" onClick={() => loadDashboard({ initial: true })}>
              <RefreshCcw size={18} />
            </IconButton>
            <IconButton
              label="Log out"
              onClick={() => {
                clearTokens();
                onLogout();
              }}
            >
              <LogOut size={18} />
            </IconButton>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-7xl px-5 py-6">
        {error && <InlineError message={error} />}

        {loading ? (
          <div className="flex min-h-[50vh] items-center justify-center">
            <Loader2 className="animate-spin text-slate-500" size={32} />
          </div>
        ) : (
          <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_390px]">
            <section className="space-y-6">
              <BalanceCards balance={balance} />
              <PayoutsTable payouts={payouts} />
              <LedgerList ledger={ledger} />
            </section>
            <aside className="space-y-6">
              <PayoutForm onCreated={() => loadDashboard({ initial: false })} />
              <ApiNote />
            </aside>
          </div>
        )}
      </div>
    </main>
  );
}

function IconButton({ label, onClick, children }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="inline-flex h-10 w-10 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
    >
      {children}
    </button>
  );
}

function BalanceCards({ balance }) {
  const cards = [
    {
      label: "Available",
      value: balance?.available_paise,
      icon: Wallet,
      tone: "bg-emerald-50 text-emerald-700 border-emerald-100",
    },
    {
      label: "Held",
      value: balance?.held_paise,
      icon: CreditCard,
      tone: "bg-amber-50 text-amber-700 border-amber-100",
    },
    {
      label: "Credited",
      value: balance?.total_credited_paise,
      icon: ArrowDownLeft,
      tone: "bg-sky-50 text-sky-700 border-sky-100",
    },
    {
      label: "Debited",
      value: balance?.total_debited_paise,
      icon: ArrowUpRight,
      tone: "bg-rose-50 text-rose-700 border-rose-100",
    },
  ];

  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {cards.map((card) => (
        <div key={card.label} className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between gap-4">
            <p className="text-sm font-medium text-slate-500">{card.label}</p>
            <div className={`flex h-10 w-10 items-center justify-center rounded-md border ${card.tone}`}>
              <card.icon size={19} />
            </div>
          </div>
          <p className="mt-4 text-2xl font-semibold text-slate-950">{formatMoney(card.value)}</p>
        </div>
      ))}
    </div>
  );
}

function PayoutForm({ onCreated }) {
  const [amount, setAmount] = useState("");
  const [bankAccountId, setBankAccountId] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  const amountPaise = useMemo(() => Math.round(Number(amount || 0) * 100), [amount]);

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    setSuccess("");

    if (!Number.isFinite(amountPaise) || amountPaise <= 0) {
      setError("Enter a payout amount greater than zero.");
      return;
    }
    if (!bankAccountId.trim()) {
      setError("Enter a bank account ID.");
      return;
    }

    setLoading(true);
    try {
      await api.createPayout({
        amount_paise: amountPaise,
        bank_account_id: bankAccountId.trim(),
      });
      setAmount("");
      setBankAccountId("");
      setSuccess("Payout request created.");
      onCreated();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={submit} className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-5">
        <h2 className="text-lg font-semibold text-slate-950">Request payout</h2>
        <p className="mt-1 text-sm text-slate-500">Funds move from available to held immediately.</p>
      </div>
      <TextField label="Amount in rupees" type="number" value={amount} onChange={setAmount} required />
      <TextField label="Bank account ID" value={bankAccountId} onChange={setBankAccountId} required />
      <button
        type="submit"
        disabled={loading}
        className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-md bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-70"
      >
        {loading ? <Loader2 className="animate-spin" size={18} /> : <Send size={18} />}
        Submit payout
      </button>
      {error && <InlineError message={error} />}
      {success && (
        <div className="mt-3 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700">
          {success}
        </div>
      )}
    </form>
  );
}

function PayoutsTable({ payouts }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-200 p-5">
        <h2 className="text-lg font-semibold text-slate-950">Payouts</h2>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-normal text-slate-500">
            <tr>
              <th className="px-5 py-3 font-semibold">ID</th>
              <th className="px-5 py-3 font-semibold">Amount</th>
              <th className="px-5 py-3 font-semibold">Status</th>
              <th className="px-5 py-3 font-semibold">Bank account</th>
              <th className="px-5 py-3 font-semibold">Created</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {payouts.length === 0 ? (
              <tr>
                <td className="px-5 py-8 text-center text-slate-500" colSpan="5">
                  No payouts yet.
                </td>
              </tr>
            ) : (
              payouts.map((payout) => (
                <tr key={payout.id} className="hover:bg-slate-50">
                  <td className="px-5 py-4 font-medium text-slate-700">{payout.id}</td>
                  <td className="px-5 py-4 text-slate-950">{formatMoney(payout.amount_paise)}</td>
                  <td className="px-5 py-4"><StatusPill status={payout.status} /></td>
                  <td className="px-5 py-4 text-slate-600">{payout.bank_account_id ?? "Not available"}</td>
                  <td className="px-5 py-4 text-slate-600">{formatDate(payout.created_at)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function LedgerList({ ledger }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-200 p-5">
        <h2 className="text-lg font-semibold text-slate-950">Recent ledger entries</h2>
      </div>
      <div className="divide-y divide-slate-100">
        {ledger.length === 0 ? (
          <p className="p-5 text-sm text-slate-500">No ledger entries yet.</p>
        ) : (
          ledger.slice(0, 8).map((entry) => (
            <div key={entry.id} className="flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-medium text-slate-950">{entry.entry_type ?? entry.type ?? "ledger_entry"}</p>
                  <StatusPill status={entry.status ?? entry.direction} />
                </div>
                <p className="mt-1 text-sm text-slate-500">{formatDate(entry.created_at)}</p>
              </div>
              <p className={`text-base font-semibold ${entry.direction === "credit" ? "text-emerald-700" : "text-slate-950"}`}>
                {entry.direction === "credit" ? "+" : "-"}
                {formatMoney(entry.amount_paise)}
              </p>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function ApiNote() {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 text-sm text-slate-600 shadow-sm">
      <h2 className="font-semibold text-slate-950">API base</h2>
      <p className="mt-2 break-all">{API_BASE_URL}</p>
      <p className="mt-3">Set `VITE_API_BASE_URL` for deployed or non-local backends.</p>
    </div>
  );
}

function App() {
  const [authenticated, setAuthenticated] = useState(Boolean(getToken()));

  return authenticated ? (
    <Dashboard onLogout={() => setAuthenticated(false)} />
  ) : (
    <AuthScreen onAuthenticated={() => setAuthenticated(true)} />
  );
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
