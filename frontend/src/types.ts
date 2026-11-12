export type ActionType = "fold" | "check" | "call" | "bet" | "raise" | "all_in";
export type Street = "preflop" | "flop" | "turn" | "river" | "showdown" | "complete";
export type SessionStatus = "waiting" | "in_hand" | "game_over";

export interface PlayerView {
  seat: number;
  name: string;
  is_human: boolean;
  stack: number;
  bet: number;
  folded: boolean;
  all_in: boolean;
  last_action: string | null;
  hole: string[] | null;
  hand_description: string | null;
  has_cards: boolean;
}

export interface LegalView {
  can_fold: boolean;
  can_check: boolean;
  can_call: boolean;
  call_amount: number;
  can_raise: boolean;
  raise_type: "bet" | "raise";
  min_raise_to: number;
  max_raise_to: number;
  raise_is_all_in: boolean;
}

export interface PotResult {
  amount: number;
  winners: number[];
  hand: string | null;
  /** Cards making the winning hand(s); empty when the pot was uncontested. */
  cards?: string[];
}

/** All-in equity of the shown-down hands at one street (keys are seat numbers). */
export interface StreetEquity {
  street: "preflop" | "flop" | "turn";
  method: "exact" | "monte_carlo";
  equity: Record<string, number>;
  std_error: Record<string, number>;
}

export interface HandResult {
  winnings: Record<string, number>;
  uncontested: boolean;
  pots: PotResult[];
  showdown: Record<
    string,
    { cards: string[]; category: string; description: string; best?: string[] }
  > | null;
  /** Present only when the hand reached a showdown. */
  equity?: StreetEquity[] | null;
  /** Decisions this hand that adaptation changed, with the tendency behind each. */
  adaptation_notes?: string[];
}

export interface GameState {
  session_id: string;
  status: SessionStatus;
  hand_number: number;
  street: Street | null;
  board: string[];
  pot: number;
  collected_pot: number;
  current_bet: number;
  button: number;
  to_act: number | null;
  players: PlayerView[];
  legal: LegalView | null;
  result: HandResult | null;
  log: string[];
  /** During an all-in with every hand face up: equity at each street reached so far. */
  live_equity?: StreetEquity[] | null;
  opponent_model?: OpponentModelView | null;
  winner: number | null;
  config: {
    starting_stack: number;
    small_blind: number;
    big_blind: number;
    difficulty?: Difficulty;
    adaptive?: boolean;
  };
}

export interface GameEvent {
  type: string;
  [key: string]: unknown;
}

export interface Step {
  event: GameEvent;
  state: GameState;
}

export interface PlayResponse {
  state: GameState;
  steps: Step[];
}

export type Difficulty = "beginner" | "intermediate" | "advanced";

export interface GameConfig {
  starting_stack: number;
  small_blind: number;
  big_blind: number;
  player_name: string;
  difficulty: Difficulty;
  /** Let the opponent adapt to your observed tendencies (Intermediate and Advanced). */
  adaptive: boolean;
}

export interface TrackedStat {
  key: string;
  label: string;
  hint: string;
  /** Observed frequency (null before the first opportunity). Older hands count less. */
  rate: number | null;
  count: number;
  opportunities: number;
  /** Shrunk estimate the opponent actually uses. */
  estimate: number;
  baseline: number;
  reliable: boolean;
}

export interface OpponentModelView {
  adapts: boolean;
  weight: number;
  policy: string;
  hands: number;
  min_opportunities: number;
  stats: TrackedStat[];
  adjustments: string[];
}

// ------------------------------------------------------------------ coaching (v0.3.5)

export type Grade = "good" | "close" | "questionable" | "mistake" | "ungraded";
export type Concept =
  | "pot_odds"
  | "draws"
  | "value_betting"
  | "bluffing"
  | "bet_sizing"
  | "preflop"
  | "reads";
export type TrainingDifficulty = "beginner" | "intermediate" | "advanced";

export interface CoachOption {
  key: string;
  action: string;
  amount: number | null;
  label: string;
  /** Estimated chips from the moment of the decision (fold = 0); null when not modelled. */
  ev: number | null;
  basis: "pot odds" | "fold model" | "guideline" | "not modelled";
  equity_when_called: number | null;
  fold_rate: number | null;
  detail: string;
}

export interface RangeCheck {
  name: "tighter" | "looser";
  label: string;
  description: string;
  range_size: number;
  equity: number;
  /** Estimated EV of each modelled option (chips) against this range. */
  evs: Record<string, number>;
}

export type LessonKind = "exact" | "estimate" | "assumption" | "model" | "guideline";

/** The decision as five steps (v0.4.2): what you did, knew, estimated, the alternative, the principle. */
export interface Lesson {
  did: string;
  known: string[];
  estimated: string[];
  math: { text: string; kind: LessonKind }[];
  alternative: string;
  principle: string;
  also: string[];
}

export interface TendencyEvidence {
  key: string;
  phrase: string;
  estimate: number;
  low: number;
  high: number;
  reference: number;
  seen: number;
  reliable: boolean;
  used: boolean;
  clear: boolean;
}

export interface OpponentInfo {
  evidence: TendencyEvidence[];
  used: string[];
  style: string[];
  note: string;
}

export interface DecisionReview {
  street: string;
  hole: string[];
  board: string[];
  hand: string;
  position: string;
  pot: number;
  to_call: number;
  stack: number;
  big_blind: number;
  history: string[];
  spot: "facing_bet" | "unbet" | "unopened";
  concept: Concept;
  equity: number | null;
  equity_se: number | null;
  equity_method: string | null;
  required_equity: number | null;
  range_size: number | null;
  range_steps: string[];
  reads: string[];
  options: CoachOption[];
  best: string[];
  chosen: string | null;
  grade: Grade;
  grade_label: string;
  confidence: "low" | "medium" | "high";
  ev_loss: number;
  tags: string[];
  headline: string;
  explanation: string;
  details: string[];
  caveats: string[];
  /** The same comparison against a tighter and a looser opponent range (v0.4.1+). */
  range_check?: RangeCheck[];
  /** Whether the verdict holds against every range in ``range_check``. */
  robust?: boolean | null;
  /** v0.4.2: no more betting can follow, so equity is realised in full. */
  betting_closed?: boolean;
  /** v0.4.2: share of its equity a call must realise to break even (before the river). */
  realization_needed?: number | null;
  /** v0.4.2: what the coach knows about the opponent, with sample sizes. */
  opponent?: OpponentInfo | null;
  /** v0.4.2: the explanation as steps (absent on reviews saved by older versions). */
  lesson?: Lesson | Record<string, never>;
  /** The decision point as public data, for "practise this spot". */
  spot_data?: Record<string, unknown>;
}

export interface HandReview {
  hand_number: number;
  decisions: DecisionReview[];
  counts: Record<Grade, number>;
  net: number;
  net_bb: number;
  ev_loss_bb: number;
  quality: "sound" | "mixed" | "costly" | "none";
  verdict: string;
  hindsight: string | null;
  tags: string[];
}

// ------------------------------------------------------------------ training (v0.3.6)

export interface TrainingScenario {
  id: string;
  difficulty: TrainingDifficulty;
  concept: Concept;
  concept_label: string;
  concept_blurb: string;
  hero: string[];
  board: string[];
  street: string;
  position: string;
  pot: number;
  to_call: number;
  stack: number;
  opponent_stack: number;
  big_blind: number;
  history: string[];
  hand: string;
  choices: { key: string; label: string }[];
  opponent: {
    name: string;
    stats: { key: string; label: string; rate: number | null; opportunities: number; baseline: number }[];
  } | null;
  hints: string[];
  /** Set when the situation was fixed to practise a leak (v0.4.4). */
  focus?: Focus | null;
  focus_label?: string | null;
}

export interface TrainingAlternative {
  key: string;
  label: string;
  ev: number | null;
  ev_bb: number | null;
  basis: CoachOption["basis"];
  grade: Grade;
  grade_label: string;
  explanation: string;
  fold_rate: number | null;
  equity_when_called: number | null;
  detail: string;
}

export interface TrainingAnswer {
  id: string;
  concept: Concept;
  choice: string;
  correct: boolean;
  acceptable: boolean;
  analysis: DecisionReview;
  alternatives: TrainingAlternative[];
}

// ------------------------------------------------------------------ hand history (v0.4.3)

/** A completed hand as public data (see backend ``pokerlab/history.py``). */
export interface HandRecord {
  v: 1;
  id: string;
  t: number;
  session: string;
  hand_number: number;
  mode: "play";
  difficulty: string | null;
  adaptive: boolean | null;
  hero: number;
  names: string[];
  hero_cards: string[];
  /** Opponent cards by seat, only when they were shown (null otherwise). */
  opponent_cards: Record<string, string[] | null>;
  board: string[];
  events: ({ type: string; decision?: number } & Record<string, unknown>)[];
  decisions_linked: boolean;
  coaching: HandReview | null;
}

/** The table after one event, rebuilt by the server from the recorded events. */
export interface HandFrame {
  index: number;
  type: string;
  text: string | null;
  street: string;
  board: string[];
  pot: number;
  bets: number[];
  stacks: number[];
  folded: boolean[];
  actor: number | null;
  /** Index into ``coaching.decisions`` for the player's own actions. */
  decision: number | null;
  to_call: number | null;
  /** Opponent cards shown so far (by seat). */
  visible: Record<string, string[]>;
  awarded: Record<string, number> | null;
}

export interface HandSummaryDecision {
  i: number;
  street: string;
  spot: string;
  action: string | null;
  grade: Grade;
  confidence: "low" | "medium" | "high";
  /** Whether the verdict held for the tighter and looser range (v0.4.4 summaries). */
  robust?: boolean | null;
  tags: string[];
  concept: Concept;
  pot: number;
  to_call: number;
  stack: number;
  equity: number | null;
  required: number | null;
  ev_loss: number;
}

export interface HandSummary {
  id: string;
  t: number;
  hand_number: number;
  session: string;
  difficulty: string | null;
  position: string;
  big_blind: number;
  hero_cards: string[];
  opponent_cards: string[] | null;
  board: string[];
  hand_class: string;
  hand_percentile: number;
  final_hand: string | null;
  final_category: string | null;
  net: number;
  net_bb: number;
  pot: number;
  pot_bb: number;
  result: "won" | "lost" | "split" | "even";
  showdown: boolean;
  last_street: string;
  flagged: number;
  quality: HandReview["quality"] | null;
  ev_loss_bb: number | null;
  tags: string[];
  decisions: HandSummaryDecision[];
}

// ------------------------------------------------------------------ leaks (v0.4.4)

/** A focus fixes a generated scenario's situation (never its answer). */
export type Focus = "large_bets" | "river";

export type LeakStatus = "leak" | "uncertain" | "possible" | "strength" | "ok" | "insufficient";

/** A decision that supports (or weakens) a finding: decision-time facts only. */
export interface LeakHand {
  id: string;
  hand_number: number;
  t: number;
  decision: number | null;
  street: string;
  action: string | null;
  grade: Grade;
  confidence: "low" | "medium" | "high";
  equity: number | null;
  required: number | null;
  pot: number;
  to_call: number;
  big_blind: number;
  hero_cards: string[];
  /** "flagged" (counts as evidence) or "uncertain" (depended on an assumption). */
  kind: "flagged" | "uncertain";
}

export interface RateComparison {
  before: { flagged: number; opportunities: number; rate: number | null };
  after: { flagged: number; opportunities: number; rate: number | null };
  change: "improving" | "worsening" | "no_clear_change" | "insufficient";
}

export interface LeakItem {
  key: string;
  title: string;
  description: string;
  concept: Concept;
  status: LeakStatus;
  opportunities: number;
  flagged: number;
  uncertain: number;
  /** Opportunities the coach graded with medium or high confidence. */
  judged: number;
  rate: number | null;
  low: number;
  high: number;
  hands: LeakHand[];
  missing?: string[];
  split?: { calls: number; folds: number };
  conditions?: {
    key: string;
    label: string;
    flagged: number;
    opportunities: number;
    rate: number;
    rate_elsewhere: number;
    opportunities_elsewhere: number;
    focus: Focus | null;
  }[];
  trend?: RateComparison;
  practice?: {
    answers: number;
    score: number | null;
    recent_score: number | null;
    first_at: number | null;
    last_at: number | null;
    since: RateComparison | null;
  };
  recommendation?: { concept: Concept; focus: Focus | null; focus_label: string | null; text: string; hands: LeakHand[] };
}

export interface LeakReport {
  hands: number;
  decisions: number;
  confident_decisions: number;
  status: "ready" | "insufficient";
  needed: number;
  leaks: LeakItem[];
  strengths: LeakItem[];
  watch: LeakItem[];
  other: LeakItem[];
  window: number;
  rules: {
    min_opportunities: number;
    min_flagged: number;
    min_rate: number;
    strength_min_opportunities: number;
    strength_max_rate: number;
    trend_min: number;
    interval: string;
    min_graded: number;
  };
}
