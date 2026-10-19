"""Usage metering: the durable record of what each organization consumed.

It records; it does not price, hold a balance or take money. Three meters,
all in whole natural units:

* ``compute``       seconds a rented machine was useful to a job
* ``analysis_run``  plugin runs that completed
* ``storage``       bytes an organization held on a UTC day (byte-days)

Layout:

* :mod:`timeutil`  UTC, whole seconds, the one local-time conversion
* :mod:`rules`     pure functions: source facts -> quantity (no Frappe)
* :mod:`ledger`    the only writer of ``WebODM Usage Record``
* :mod:`storage`   the daily measurement of stored bytes
* :mod:`sweep`     catch-up recording of closed machines and finished runs
* :mod:`hooks`     last-chance capture when a source is about to be deleted
* :mod:`report`    "what did this organization consume in this period?"
* :mod:`verify`    re-derive every record from its own evidence
* :mod:`guard`     isolation for metering work on someone else's code path

The design record, the decisions and how to check any figure yourself are in
``docs/metering/README.md``.

This file imports nothing on purpose: ``hooks`` is loaded by Frappe on the
delete path of other documents and must stay importable whatever state the
rest of the package is in.
"""
