# Open-Pyxel Security Release Gates

## Public Network Release Requirement

The permanent public Open-Pyxel network must not open until security validation is complete.

## Development Beta Phase

Before public release:

- Operate a restricted beta network.
- Keep enrollment controlled.
- Limit initial deployment to fewer than 100 devices.
- Enable full security logging and monitoring.
- Use beta results to improve architecture and threat models.

## Heavy Red-Team Review

Required before public release:

- Identity impersonation testing
- Authentication bypass testing
- Replay attack testing
- Protocol manipulation testing
- Enrollment abuse testing
- Revocation bypass testing
- Peer discovery abuse testing
- Resource exhaustion testing
- Compute contribution fraud testing
- Reputation manipulation testing

Acceptance requirement:

Successful impersonation attacks: 0/*

Successful replay attacks: 0/*

Any successful identity compromise blocks release.

## Penetration Testing

Test targets:

- Node runtime
- Coordinator services
- Networking protocol
- Authentication systems
- Enrollment systems
- Storage systems
- Update mechanisms
- APIs
- Administrative interfaces

Required outputs:

- vulnerability report
- severity classification
- remediation plan
- retest confirmation

## Release Gate

Public launch requires:

- Cryptography complete
- Authentication complete
- Enrollment hardened
- Compute verification complete
- Red-team review complete
- Penetration testing complete
- Critical findings resolved
- Beta network completed
- Final architecture review approved
