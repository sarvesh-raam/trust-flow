---
title: Trust Flow
emoji: 📦
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
short_description: Customs compliance agent for invoices and bills of lading
---

# Trust Flow

Autonomous customs compliance: upload a commercial invoice and bill of lading,
and a LangGraph pipeline extracts the fields, reconciles the two documents,
classifies HS codes, applies country rules and drafts the declaration — pausing
for human review when something blocks.

Source code: https://github.com/sarvesh-raam/trust-flow

Open the app at its direct address (`https://<owner>-<space>.hf.space`) rather
than inside the Hugging Face page, so Google sign-in works.

Data on this demo is not permanent: uploads and runs are cleared whenever the
Space restarts.
