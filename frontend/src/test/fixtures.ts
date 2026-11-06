import type { GameState, LegalView, PlayerView } from "../types";

export function player(overrides: Partial<PlayerView> = {}): PlayerView {
  return {
    seat: 0,
    name: "You",
    is_human: true,
    stack: 990,
    bet: 10,
    folded: false,
    all_in: false,
    last_action: null,
    hole: ["As", "Kd"],
    hand_description: null,
    has_cards: true,
    ...overrides,
  };
}

export function legal(overrides: Partial<LegalView> = {}): LegalView {
  return {
    can_fold: true,
    can_check: false,
    can_call: true,
    call_amount: 5,
    can_raise: true,
    raise_type: "raise",
    min_raise_to: 20,
    max_raise_to: 1000,
    raise_is_all_in: true,
    ...overrides,
  };
}

export function gameState(overrides: Partial<GameState> = {}): GameState {
  return {
    session_id: "abc",
    status: "in_hand",
    hand_number: 1,
    street: "preflop",
    board: [],
    pot: 15,
    collected_pot: 0,
    current_bet: 10,
    button: 0,
    to_act: 0,
    players: [
      player({ bet: 5, stack: 995 }),
      player({ seat: 1, name: "Dealer Bot", is_human: false, hole: null, bet: 10 }),
    ],
    legal: legal(),
    result: null,
    log: ["Hand #1 — You have the button"],
    winner: null,
    config: { starting_stack: 1000, small_blind: 5, big_blind: 10 },
    ...overrides,
  };
}
