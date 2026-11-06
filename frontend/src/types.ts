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
}

export interface HandResult {
  winnings: Record<string, number>;
  uncontested: boolean;
  pots: PotResult[];
  showdown: Record<string, { cards: string[]; category: string; description: string }> | null;
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
  winner: number | null;
  config: { starting_stack: number; small_blind: number; big_blind: number };
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

export interface GameConfig {
  starting_stack: number;
  small_blind: number;
  big_blind: number;
  player_name: string;
}
