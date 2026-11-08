import { type ConceptSkill, LEVEL_LABEL } from "../lib/skills";

const pct = (x: number) => `${Math.round(x * 100)}%`;

/** "7 of 10 first answers good, 1 close" (measured), or "not tried". */
export function measuredText(s: ConceptSkill): string {
  if (s.attempts === 0) return "not tried";
  const close = s.close ? `, ${s.close} close` : "";
  return `${s.good} of ${s.attempts} good${close}`;
}

/**
 * Estimated skill: the expected first-try score on an Intermediate scenario as a point
 * inside its 80% interval. Below the evidence threshold it says so instead of guessing.
 */
export function SkillMeter({ skill }: { skill: ConceptSkill }) {
  const { score } = skill;
  if (!skill.enough) {
    return (
      <div className="mt-1 text-[11px] text-slate-400" data-testid="skill-insufficient">
        Not enough answers for an estimate yet
      </div>
    );
  }
  return (
    <div className="mt-1">
      <div
        className="relative h-2 rounded-full bg-white/10"
        role="meter"
        aria-label={`${skill.label} estimated skill`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(score.mean * 100)}
        aria-valuetext={`about ${pct(score.mean)}, likely between ${pct(score.low)} and ${pct(score.high)}`}
        title={`Estimated: about ${pct(score.mean)} on Intermediate scenarios (80% range ${pct(score.low)}–${pct(score.high)})`}
      >
        <div
          className="absolute inset-y-0 rounded-full bg-amber-300/30"
          style={{ left: `${score.low * 100}%`, width: `${(score.high - score.low) * 100}%` }}
        />
        <div
          className="absolute top-1/2 h-3 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-amber-300"
          style={{ left: `${score.mean * 100}%` }}
        />
      </div>
      <div className="mt-0.5 flex justify-between text-[11px] text-slate-400">
        <span data-testid="skill-estimate">
          Est. {pct(score.mean)} ({pct(score.low)}–{pct(score.high)})
        </span>
        <span>Level: {LEVEL_LABEL[skill.level]}</span>
      </div>
    </div>
  );
}
