import { useEffect, useMemo, useState } from "react";

type ProgressRingSize = "toast" | "compact" | "large";

interface ProgressRingProps {
  value: number;
  label: string;
  size?: ProgressRingSize;
  className?: string;
}

export function ProgressRing({ value, label, size = "compact", className = "" }: ProgressRingProps) {
  const progress = Math.max(0, Math.min(100, Math.round(value || 0)));
  const [displayValue, setDisplayValue] = useState(0);
  const classNames = ["progress-ring", `progress-ring-${size}`, className].filter(Boolean).join(" ");
  const strokeOffset = useMemo(() => Number((100 - displayValue).toFixed(2)), [displayValue]);

  useEffect(() => {
    const timerId = window.setInterval(() => {
      setDisplayValue((current) => {
        if (progress < current) {
          window.clearInterval(timerId);
          return progress;
        }
        if (current >= progress) {
          window.clearInterval(timerId);
          return progress;
        }
        const remaining = progress - current;
        const step = Math.max(0.8, Math.min(4, remaining * 0.18));
        return Math.min(progress, Number((current + step).toFixed(2)));
      });
    }, 40);

    return () => window.clearInterval(timerId);
  }, [progress]);

  return (
    <div
      className={classNames}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={progress}
      aria-label={`${label}，进度 ${progress}%`}
      data-progress-target={progress}
      data-progress-display={Math.round(displayValue)}
    >
      <svg className="progress-ring-svg" viewBox="0 0 48 48" aria-hidden="true">
        <circle className="progress-ring-track" cx="24" cy="24" r="18" pathLength={100} />
        <circle
          className="progress-ring-value"
          cx="24"
          cy="24"
          r="18"
          pathLength={100}
          strokeDasharray="100"
          strokeDashoffset={strokeOffset}
        />
      </svg>
      <span className="progress-ring-scan" aria-hidden="true" />
    </div>
  );
}
