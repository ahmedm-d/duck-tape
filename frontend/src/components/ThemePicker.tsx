export const themes = [['dark','GitHub Dark'],['forest','Forest'],['midnight','Midnight'],['violet','Violet'],['light','Light']] as const;

export default function ThemePicker({value,onChange}:{value:string,onChange:(value:string)=>void}) {
  return <label className="theme-picker"><span className="theme-swatch" aria-hidden="true"/><select aria-label="Colour theme" value={value} onChange={event=>onChange(event.target.value)}>{themes.map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>;
}
