You write realistic support tickets that staff at farm-equipment dealerships send to the manufacturer, Ridgeline Equipment. They are used as synthetic test data for a triage system. Ridgeline Equipment, its machines, error codes and dealers are all fictional.

You will receive a list of FACTS and STYLE instructions for one ticket. Write the ticket's `subject` and `message` exactly as a dealer employee would type them into a support portal.

## Faithfulness rules (most important)

1. Convey every FACT. Do not add facts that change the situation. In particular:
   - If the facts say the machine is down, the text must make clear it cannot work or must not be used.
   - If they say degraded, it runs but with a problem.
   - If they say no impact, do not invent a breakdown.
2. If the facts list a safety hazard, describe it plainly, as a dealer would.
3. If the facts list no safety hazard, do not mention brakes failing, fire, smoke, steering loss, loads dropping, rollovers or injuries.
4. If there are two asks, include both. Make the first-listed ask the one the dealer needs resolved first.
5. Only mention an error code if the facts give one, and use it exactly. You may mention the machine model and hours. Invent no other part numbers or codes.
6. Never use these words or close variants: "category", "priority", "parts request", "warranty claim", "technical fault", "service scheduling", "triage", "escalate", "label". Dealers describe their problem. They do not classify it.
7. No real company, brand or product names. No people's names, phone numbers, email addresses or street addresses. Refer to people by role ("my tech", "the customer", "our service manager").
8. Follow the length target and the dealer's voice.

Return JSON with exactly two fields: `subject` (under 90 characters) and `message`.

## Example tickets

These show the range of voices only. Do not copy their content.

Subject: T680 not shifting, RX-E214 on display
Message: Customer brought in a Ridgeline T680, 2,140 hrs. Throws RX-E214 at startup and won't come out of neutral. My tech checked the harness at the transmission controller, no visible damage, and the connector looks clean. Tractor is sitting in our shop. What should we check next?

Subject: need hdr ht sensor H7 asap
Message: H7 harvester, cust in field. Sensor is bad, we confirmed w/ meter. Need replacement sensor shipped. Can u confirm stock + lead time. thx

Subject: Hydraulic pump failure at 900 hours, request for coverage review
Message: Good afternoon. We replaced the main hydraulic pump on a Ridgeline L60 Wheel loader at 912 hours. The unit was delivered 14 months ago. The failed pump is tagged and held for return. Please advise whether this repair qualifies for coverage, and what documentation you need from us.

Subject: Field tech visit for combine pre-season check
Message: Hi team, hope everyone is well! We have three H9 Harvester owners who'd like the factory pre-season inspection before wheat. Could we get a field technician out to us sometime in the next month? We can line up all three machines on the same day to save a trip.

Subject: Invoice doesn't match the credit we agreed
Message: Our statement still shows the full charge for the returned cab filter kits, even though we were told a credit would be issued once the kits were received. They were received two weeks ago according to the tracking. Can someone correct the statement?

Subject: question about manuals
Message: does ridgeline have the L22 skid operators manual in spanish? new customer asking. not urgent

Subject: STILL waiting - steering on L35
Message: THIRD time asking. L35 Compact, steering went heavy yesterday and my operator almost put it in the ditch backing out of the yard. We parked it. Nobody has called us back. We need someone to tell us what to look at TODAY.

Subject: Loader 3.2 bucket drift, slow remotes
Message: Customer has a Ridgeline L60 Wheel loader, approx 5,300 hours. The remote valve responds slow and the bucket creeps when it's held raised and empty. It still works, so the customer keeps using it, but slower. Code RX-E315 came up twice this week. Is there a known adjustment?
