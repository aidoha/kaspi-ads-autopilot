type Props = {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  /** Текст для скринридера — конкретный, а не общее «переключатель». */
  label: string;
};

/** iOS-тоггл из макета (.switch/.track/.knob,
 *  docs/design/autopilot-mockup.html). Скрытый чекбокс поверх дорожки
 *  и кружка — так работает клавиатурный фокус и клик мышью по всей
 *  площади переключателя. */
export default function Switch({ checked, onChange, disabled, label }: Props) {
  return (
    <label className="switch" title={label}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-label={label}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="track" />
      <span className="knob" />
    </label>
  );
}
