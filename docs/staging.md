# Staging site on Azure (UAE North)

A staging site is a private copy of the platform at a normal web address, for teachers and leaders to try. It runs on one small server in Microsoft Azure's UAE North region (Dubai) and holds **only the fictional demo school**: never load real students into staging (spec §10.3).

It costs about **US$40–50 a month** while the server exists. You can switch it off when nobody is using it (see [Saving money](#saving-money)).

## What you need

- A Microsoft Azure account with a subscription. New accounts at https://azure.microsoft.com/free get free credit for the first month.
- About 20 minutes. Most of it is waiting.

Nothing to install: everything runs in your browser.

## Create the site

1. Go to **https://shell.azure.com** and sign in. If asked, choose **Bash**, not PowerShell. If it asks to create storage for Cloud Shell, accept the defaults, or choose "No storage account required".
2. Paste this line and press **Enter**:
   ```
   curl -fsSL https://raw.githubusercontent.com/AbduSamadh/StudentSkillTracker/main/deploy/staging/azure-create.sh | bash
   ```
3. Wait. It creates the server (2–3 minutes), and then the server installs the site (about 10–15 minutes). Dots appear while it waits.
4. When it prints **Ready: https://stemtrack-xxxxxx.uaenorth.cloudapp.azure.com**, open that address. That's your staging site; share it with the people testing it.

The login details are printed at the end and are the same as in the demo:
- School code `demo`; password `Demo-Password-2026!`.
- Leaders and admins also need a 6-digit code. Add the key printed next to their account to an authenticator app (Google or Microsoft Authenticator → *Enter a setup key*).

If it says *Not answering yet*, wait a few minutes and open the address anyway. The command it prints next to that message shows the server's setup log.

**If the server can't be created** ("size not available" or "quota"), some new subscriptions don't offer the default server size in UAE North. Try a different size by pasting this instead:
```
curl -fsSL https://raw.githubusercontent.com/AbduSamadh/StudentSkillTracker/main/deploy/staging/azure-create.sh | VM_SIZE=Standard_B2als_v2 bash
```

## Update it to the latest version

Paste the same command from step 2 into Cloud Shell again. When the server already exists, it pulls the latest code and restarts the site, in about 5–10 minutes. Demo data is kept.

## Saving money

These commands work in Cloud Shell:

| To | Paste |
|---|---|
| Switch the server off. You stop paying for it; storage costs a few dollars a month. | `az vm deallocate -g stemtrack-staging -n stemtrack-staging` |
| Switch it on again. The site comes back by itself in a few minutes, at the same address. | `az vm start -g stemtrack-staging -n stemtrack-staging` |
| Delete everything for good | `az group delete -n stemtrack-staging` |

## How it works

For whoever looks after it:

- **The server.** It's an Ubuntu 24.04 virtual machine, size `Standard_B2s` (2 vCPUs, 4 GB). It is in resource group `stemtrack-staging` in `uaenorth`, with a free Azure DNS name.
- **Install.** On first boot, cloud-init installs Docker, clones the repository into `/opt/stemtrack`, and runs [`deploy/staging/setup.sh`](../deploy/staging/setup.sh).
- **What `setup.sh` runs.** The same Docker stack as the demo, plus [`docker-compose.staging.yml`](../deploy/staging/docker-compose.staging.yml):
  - [Caddy](https://caddyserver.com) gets and renews a free HTTPS certificate.
  - The app runs in `staging` mode, which refuses development secrets and marks session cookies `Secure`.
  - Only ports 80 and 443 are open to the web.
- **Secrets.** These are generated once on the server into `/opt/stemtrack/.env`, readable only by root and never in git.
- **Restarts.** Every service restarts automatically after a reboot.
- **SSH isn't needed.** Updates and logs go through Azure's *run command*. Cloud Shell also created an SSH key for user `stemadmin` (`~/.ssh/id_rsa`), but it's only kept if Cloud Shell has storage.
- **Testing.** CI runs the same `setup.sh` on every change (job *Staging kit*). It checks HTTPS, staging mode, secure cookies, HSTS, that the internal port is closed, and that a re-run is safe.

Staging is deliberately simpler than production. It has no managed database with point-in-time recovery, no backups (its data is fictional and can be re-created), and no email, SMS or WhatsApp sending. Messages appear in the parent portal's inbox. [Operations](operations.md) describes production.
