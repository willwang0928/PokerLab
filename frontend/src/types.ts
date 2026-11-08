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
