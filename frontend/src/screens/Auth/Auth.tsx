import { useState, type FormEvent } from "react";
import { useDispatch } from "../../state/DuelContext";
import { Logo } from "../../components/Logo";
import { Wordmark } from "../../components/Wordmark";
import { api, auth, type AuthResponse } from "../../hooks/api";
import styles from "./Auth.module.css";

/** FastAPI returns a string detail for app errors, but a list of objects for
    422 validation errors — surface something readable in both cases. */
function errMsg(data: unknown): string {
  const detail = (data as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && typeof detail[0]?.msg === "string")
    return detail[0].msg;
  return "Something went wrong.";
}

export function Auth() {
  const dispatch = useDispatch();
  const [registering, setRegistering] = useState(false);
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    const body = registering
      ? { username: username.trim(), email: email.trim(), password }
      : { username: username.trim(), password };
    const res = await api<AuthResponse>(
      registering ? "/auth/register" : "/auth/login",
      body,
    );
    setBusy(false);
    if (!res.ok || !res.data) {
      setError(errMsg(res.data));
      return;
    }
    auth.markAuthed(); // cookies are already set by the response; this is the JS-side hint
    dispatch({ type: "SET_ME", me: res.data.user });
    dispatch({ type: "SET_SCREEN", screen: "lobby" });
  }

  return (
    <div className={styles.screen}>
      <div className={styles.wrap}>
        <div className={styles.brand}>
          <Logo />
          <Wordmark />
        </div>
        <div className={styles.card}>
          <h2 className={styles.title}>
            {registering ? "Create account" : "Log in"}
          </h2>
          <p className={styles.sub}>
            {registering
              ? "Claim a handle and a starting rating of 1200."
              : "Welcome back to the arena."}
          </p>
          <form onSubmit={submit}>
            <input
              className={styles.field}
              placeholder="username"
              autoComplete="username"
              required
              minLength={3}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
            {registering && (
              <input
                className={styles.field}
                placeholder="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            )}
            <input
              className={styles.field}
              placeholder="password"
              type="password"
              autoComplete={registering ? "new-password" : "current-password"}
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button type="submit" className={styles.submit} disabled={busy}>
              {busy ? "…" : registering ? "Register" : "Log in"}
            </button>
            {error && <p className={styles.error}>{error}</p>}
          </form>
          <p className={styles.foot}>
            <button
              type="button"
              onClick={() => {
                setRegistering((r) => !r);
                setError("");
              }}
            >
              {registering
                ? "Have an account? Log in"
                : "No account? Register"}
            </button>
          </p>
        </div>
      </div>
    </div>
  );
}
