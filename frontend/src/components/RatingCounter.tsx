import { useEffect, useState } from "react";

/* Eases the displayed rating from -> to over ~900ms (cubic). Purely cosmetic:
   it animates the *display* only; the final value is reachable immediately and
   nothing gates on the tween finishing. */
export function RatingCounter({
  from,
  to,
  className,
}: {
  from: number;
  to: number;
  className?: string;
}) {
  const [value, setValue] = useState(from);

  useEffect(() => {
    const start = performance.now();
    const dur = 900;
    let raf = 0;
    const tick = (now: number) => {
      const k = Math.min(1, (now - start) / dur);
      const eased = 1 - Math.pow(1 - k, 3);
      setValue(Math.round(from + (to - from) * eased));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [from, to]);

  return <span className={className}>{value}</span>;
}
