# Deploying Trust Flow on an Oracle Cloud Always-Free server

The result: Trust Flow running 24/7 at `https://<your-ip>.sslip.io`, with HTTPS,
on a free Oracle Cloud VM (Ampere, up to 4 CPUs / 24 GB RAM). Data is kept
across restarts. Total time: about 45 minutes, most of it waiting.

What runs on the server (`docker compose`):

- **app** — the FastAPI backend serving the built React frontend (this folder's `Dockerfile`)
- **caddy** — web server that gets a free HTTPS certificate automatically

You need: an Oracle Cloud account, your Groq API key, and access to the
Firebase project (`trustflow-1b008`) to allow the new address for Google sign-in.

---

## 1. Create the Oracle Cloud account

1. Go to <https://signup.cloud.oracle.com> and sign up for **Free Tier**.
   A card is required to verify identity; Always-Free resources are not charged.
2. Pick a **home region** close to you (e.g. *India South (Hyderabad)* or *India West (Mumbai)*).
   It can't be changed later.

## 2. Create the server

In the Oracle console: **☰ Menu → Compute → Instances → Create instance**.

| Setting | Value |
|---|---|
| Name | `trustflow` |
| Image | **Change image → Canonical Ubuntu 24.04** |
| Shape | **Change shape → Ampere → VM.Standard.A1.Flex**, **2 OCPUs, 12 GB memory** (shows "Always Free-eligible") |
| Networking | keep the defaults; *Assign a public IPv4 address* = **Yes** |
| Add SSH keys | **Generate a key pair for me → Save private key** |

Save the downloaded private key as `C:\Users\<you>\.ssh\trustflow.key`, then click **Create**.

> **"Out of capacity"?** Free Ampere servers are popular. Try another
> *Availability domain* in the same form, pick 1 OCPU / 6 GB, or retry later.

When the instance is **Running**, copy its **Public IP address** (e.g. `129.154.10.20`).
Your app address will be that IP with dots replaced by dashes plus `.sslip.io`:
`129-154-10-20.sslip.io`.

## 3. Open ports 80 and 443

On the instance page: **Primary VNIC → Subnet → Security Lists → Default Security List
→ Add Ingress Rules**, and add two rules:

| Source CIDR | IP Protocol | Destination Port |
|---|---|---|
| `0.0.0.0/0` | TCP | `80` |
| `0.0.0.0/0` | TCP | `443` |

## 4. Connect to the server

In **PowerShell** on your PC:

```powershell
icacls $HOME\.ssh\trustflow.key /inheritance:r /grant:r "$($env:USERNAME):R"
ssh -i $HOME\.ssh\trustflow.key ubuntu@<PUBLIC-IP>
```

Type `yes` when asked about the fingerprint. Everything below runs on the server.

## 5. Install Docker and open the server firewall

```bash
# Oracle's Ubuntu images block everything but SSH at the OS level too
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save

curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
exit
```

Reconnect with the same `ssh` command (so the Docker permission takes effect).

## 6. Configure and start Trust Flow

```bash
git clone https://github.com/sarvesh-raam/trust-flow.git
cd trust-flow/deploy
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # copy the output
nano .env
```

In the editor fill in:

- `DOMAIN=` your address, e.g. `129-154-10-20.sslip.io`
- `GROQ_API_KEY=` your Groq key
- `JWT_SECRET_KEY=` the random string you just printed

Save with **Ctrl+O, Enter**, exit with **Ctrl+X**. Then build and start:

```bash
docker compose up -d --build
```

The first build takes **15–25 minutes** (it installs the OCR engine and
downloads its models). Follow progress with `docker compose logs -f app`
and press **Ctrl+C** to stop watching. It's ready when you see
`Application startup complete`.

## 7. Allow the address for Google sign-in

In the [Firebase console](https://console.firebase.google.com) → project
**trustflow-1b008** → **Authentication → Settings → Authorized domains → Add domain**,
add your address (e.g. `129-154-10-20.sslip.io`).

## 8. Open the app

Go to `https://<your-address>` (e.g. `https://129-154-10-20.sslip.io`) and sign in.
The first visit may take a few seconds while the HTTPS certificate is issued.

---

## Everyday operations

All from `~/trust-flow/deploy` on the server:

| Task | Command |
|---|---|
| Deploy the latest code from GitHub | `git pull && docker compose up -d --build` |
| See logs | `docker compose logs -f app` |
| Restart | `docker compose restart app` |
| Status | `docker compose ps` |
| Stop everything | `docker compose down` (data is kept in the `app_data` volume) |

## Troubleshooting

- **Browser can't connect at all** — recheck step 3 (security list) and step 5 (iptables).
- **"Your connection is not private" / certificate errors** — `docker compose logs caddy`;
  usually ports 80/443 aren't open yet or `DOMAIN` has a typo.
- **Google sign-in popup closes with an error** — the address isn't in Firebase's
  authorized domains (step 7).
- **Runs fail with "usage limit was reached"** — Groq's free daily token limit;
  the app already falls back to a smaller model, otherwise wait or upgrade at console.groq.com.
- **Build fails on `pip install`** — run `docker compose build --no-cache app` and share the error.
