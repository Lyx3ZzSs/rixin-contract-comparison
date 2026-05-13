import { FormEvent, useState } from "react";
import type { CSSProperties } from "react";

import loginBackground from "../picture/登录背景图.png";

interface LoginPageProps {
  onLogin: (username: string, password: string) => boolean;
}

export function LoginPage({ onLogin }: LoginPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const ok = onLogin(username.trim(), password);
    if (!ok) {
      setError("用户名或密码错误。");
    }
  }

  return (
    <main className="login-page" style={{ "--login-bg": `url("${loginBackground}")` } as CSSProperties}>
      <section className="login-slogan" aria-label="登录页标语">
        <h1>合同规范管理  合作高效共赢</h1>
        <p>严控风险.提升效率.保障合规.驱动价值</p>
      </section>

      <section className="login-card" aria-labelledby="login-title">
        <div className="login-card-header">
          <p className="eyebrow">账户登录</p>
          <h2 id="login-title">欢迎回来</h2>
        </div>
        <form onSubmit={handleSubmit}>
          <label className="field-row">
            <span>用户名</span>
            <input
              autoComplete="username"
              autoFocus
              name="username"
              placeholder="请输入用户名"
              value={username}
              onChange={(event) => {
                setUsername(event.target.value);
                setError("");
              }}
            />
          </label>
          <label className="field-row">
            <span>密码</span>
            <input
              autoComplete="current-password"
              name="password"
              placeholder="请输入密码"
              type="password"
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
                setError("");
              }}
            />
          </label>
          <button className="primary-action login-action" type="submit">
            登录系统
          </button>
          <p className={error ? "status-line error" : "status-line"} role="status">
            {error || "测试账号：admin / 123456"}
          </p>
        </form>
      </section>
    </main>
  );
}
