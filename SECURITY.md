## 🔒 Security Policy

We take the security of the **Home Assistant ESB Smart Meter Integration** and its users seriously. This document outlines the process for reporting security vulnerabilities.

### Reporting a Vulnerability via GitHub Issues

For prompt reporting and maintenance management, we ask that you use a standard GitHub Issue, following these crucial steps to ensure the vulnerability is handled with care:

1.  **Create a New Issue:** Navigate to the [Issues tab](https://github.com/antoine-voiry/home-assistant-esb-smart-meter-integration/issues) on this repository and click "New issue."
2.  **Use the Security Label:** Immediately apply the special label: `**security-vulnerability**` to the issue. This helps maintainers quickly identify the report. You may need to ask a maintainer to apply this label if you cannot.
3.  **Use an Opaque Title:** Use a descriptive but non-specific title (e.g., "Potential Credential Handling Issue," or "Configuration Validation Flaw"). **Do not** reveal the exploit details in the title.
4.  **Information to Include (in the Issue Body):**
    * A **detailed description** of the vulnerability.
    * The **version(s)** of the integration affected (e.g., `v1.2.0`).
    * **Steps to reproduce** the issue (a proof-of-concept script or configuration details).
    * The **impact** of the vulnerability.
    * Your **contact information** (optional, but helpful if we need more details).

> **⚠️ Crucial Note:** After creating the issue, we strongly recommend you **comment on the issue asking a maintainer to convert it into a GitHub Security Advisory.** This converts the public issue into a **private draft** where the vulnerability can be discussed and fixed securely before a public patch is released.

### Our Commitment (The Response Process)

We commit to a timely and professional response process:

1.  **Acknowledgement:** We will acknowledge your report within **48 hours** via a comment on the issue.
2.  **Investigation & Draft:** We will immediately move the discussion to a **private GitHub Security Advisory draft** and begin investigating the severity and the necessary fix.
3.  **Update & Disclosure:** Once a fix is ready, we will disclose the vulnerability publicly (via the finalized GitHub Security Advisory and release notes) alongside the patched version. We will credit the reporter, unless they wish to remain anonymous.

### Supported Versions

We only provide security updates for actively maintained versions of this integration. Since this is a custom Home Assistant component, support is generally tied to our latest stable version and the current major stable version of Home Assistant.

| Integration Version | Home Assistant Version Support | Status |
| :--- | :--- | :--- |
| **`[Your Latest Version]`** | **Current Stable HA Version** | **Actively Maintained** |
| `[Your Previous Version]` | Previous Stable HA Version | **Limited Support (Critical fixes only)** |
| Older Versions | All other HA Versions | **Not Supported** |

> **Action Required:** Please update the table above with your actual version numbers (e.g., `v1.2.0`, `v1.1.0`) and the corresponding Home Assistant versions they support.