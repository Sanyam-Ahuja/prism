# Domain probe corpus

**Synthetic test input. Never compiled into `artifacts/`.**

All 20 supplied queries are Display, but PDF Appendix C section 2 evaluates
accuracy across Battery, Display, Camera and Performance. These probes exist to
prove the pipeline is domain-independent — that segmentation, categorisation,
deeplink resolution and the gates behave correctly on non-Display reference text.

They are written in the structural style of the real SIIS documents (markdown
headings, imperative UI sentences, a mix of settings/physical/destructive steps)
but the content is authored by us. They are **not** ground truth and must not be
used to claim domain coverage in the plan library: real coverage is a function of
supplied SIIS text, which we only received for Display (see M-Q4).
