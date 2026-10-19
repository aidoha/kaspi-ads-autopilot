import { useEffect, useRef, useState } from "react";

type Item = { label: string; onClick: () => void };

/** Ссылки шапки. На десктопе — строкой, как раньше; на телефоне строка не
 *  влезает рядом с названием, поэтому сворачивается в кнопку «☰» с
 *  выпадающим списком (переключение — только CSS по ширине, разметка одна). */
export default function TopMenu({ items }: { items: Item[] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Тап мимо меню закрывает его — иначе на телефоне оно висит поверх списка.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);

  return (
    <div className={open ? "topmenu open" : "topmenu"} ref={ref}>
      <button type="button" className="topmenu-btn" aria-label="Меню" aria-expanded={open}
              onClick={() => setOpen((v) => !v)}>
        <span aria-hidden="true">☰</span>
      </button>
      <div className="topmenu-items">
        {items.map((it) => (
          <button key={it.label} type="button" className="linkish"
                  onClick={() => { setOpen(false); it.onClick(); }}>
            {it.label}
          </button>
        ))}
      </div>
    </div>
  );
}
