# Worldline India contract notes

Primary source: [Worldline India Checkout](https://www.paynimo.com/paynimocheckout/docs/), reviewed 23 October 2026. The relevant sections are **Offline Verification (Request Type O)** and the Checkout `returnUrl` response/hash instructions.

## Scope and corrections

Offline verification queries an unresolved payment using its merchant reference. It differs from success-case dual verification (`S`, using the gateway transaction token) and recurring-mandate verification (`TSI`). The earlier project discussion mentioned `TSI`; this implementation corrects that choice for one-time Checkout recovery.

The source describes a POST to `returnUrl`; it does not establish the delivery/retry guarantees of the original merchant's server webhook setup. We retain “missed webhook” as the reported incident description, while the simulator implements the documented callback shape. We make no claim about Worldline webhook retries.

## Implemented contract

`offline_request()` builds the documented request fields. `parse_offline_response()` reads nested `paymentMethod.paymentTransaction`, verifies the response request type and exposes the result. The service additionally matches merchant, reference and expected amount. INR is fixed in the request and local order; the example response does not include a currency field, so the code does not pretend to verify a returned currency.

`0300` permits confirmation; `0399` records failure. Other results never create passes. Unknown results remain unresolved and ultimately require review. These are one-time Checkout semantics, not mandate status-message rules.

Callbacks use the documented pipe-separated hash construction with SHA-512, matching a WEBSH2 setup. The callback is verified before parsing its reference; an independent offline query supplies the authoritative evidence.

## Merchant-specific prerequisites not established here

- Verify the merchant's actual product/version and callback transport.
- Verify required access controls for status enquiries with Worldline onboarding support. The public example does not establish a universal authentication policy.
- The docs describe `dateTime` as the success date. With a missing result, we only know the initiation date. The simulator uses that persisted date; cross-midnight lookup rules must be confirmed before live use.
- Confirm enquiry frequency, limits, timeout/retry recommendations and late status transitions.
- Confirm merchant references map to the original system's stored references. An internal order UUID is not automatically a gateway transaction ID.

The HTTP adapter refuses non-local endpoints. Fixture compatibility and successful simulation do not establish Worldline certification or live compatibility.
