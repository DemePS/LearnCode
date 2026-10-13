import { useState } from "react";
import { login, register } from "../api";

export function AuthForm({ onAuthenticated }: { onAuthenticated: (token: string) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = mode === "login" ? await login(email, password) : await register(email, password);
      onAuthenticated(res.token);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="shell shell-centered">
      <form className="objective-form" onSubmit={handleSubmit}>
        <h1>{mode === "login" ? "Log in" : "Create an account"}</h1>
        <p className="objective-hint">
          {mode === "login" ? "Welcome back." : "At least 8 characters."} Your projects and progress
          are saved to your account.
        </p>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          autoComplete="email"
          disabled={busy}
          required
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          disabled={busy}
          required
        />
        <button className="btn btn-primary" type="submit" disabled={busy || !email.trim() || !password}>
          {busy ? "Please wait…" : mode === "login" ? "Log in" : "Create account"}
        </button>
        {error && <p className="error">{error}</p>}
        <button
          type="button"
          className="btn btn-link"
          onClick={() => {
            setMode(mode === "login" ? "register" : "login");
            setError(null);
          }}
        >
          {mode === "login" ? "Need an account? Register" : "Already have an account? Log in"}
        </button>
      </form>
    </div>
  );
}
