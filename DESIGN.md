# DESIGN.md

## The moment

The second the HUD reads 4% and you reach for UPI. That gamble — spend the last battery on a cab, or save it for a call — is the game in miniature: a resource you can't refill, a clock that won't wait, a narrator keeping it funny, and a city where a cold samosa is a logical bribe.

## The cut

I chose not to let the LLM own the numbers. It narrates; the code owns time, cash, battery and progress. When I let the model price a ride, it charged ₹700 for a ₹50 offer. Removing that authority made the game fair without making it smaller.

## The miss

The narrator wrote "₹650 vanishes from your account" while the engine had blocked the payment — the wallet held ₹500. I caught it because the HUD still said ₹0 spent. The story and the ledger disagreed. I fixed it by making guardrails replace the prose on a blocked turn, not staple a correction onto it.
