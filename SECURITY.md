# Security policy

## Scope and support

`0.2.0-rc.1` is a local-use evaluation build. There is no certified stable version, guaranteed response time, or supported public server deployment. Keep the dependency locks intact; check updated advisories before wider distribution.

Creator services are loopback-only. Only the recipient listener can be enabled on an assigned private IPv4 interface, and its HTTP traffic is unencrypted. Do not tunnel, port-forward, publish, or reverse-proxy any POPTRAP listener. Never treat a Bluetooth receipt or an accepted invite as consent to launch.

## Report privately

Use the repository's **Security → Advisories → Report a vulnerability** flow if available. If private reporting is not enabled, ask the maintainers for a private channel without disclosing the vulnerability or credentials in a public issue. Include the version, OS and browser, affected boundary, expected behavior, and safe observations from your own authorized test environment.

Do not include live invitation links, creator tickets, document keys, access headers, device identifiers, personal files, or raw logs. For ordinary bugs or documentation errors use the [issue tracker](https://github.com/cybervault-Hacky/PopTrap/issues). See [the release report](docs/phase-10.md) for the dated dependency review and remaining test gaps.
