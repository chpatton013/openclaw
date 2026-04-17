# TODO:
#   interactive script
#   needs to collect inputs to pass to lower-level bootstrapping scripts
#   gets inputs from cli arguments, then falls back to interactive input
#   the inputs we need:
#   - domain for hosted zone
#   - secret authentik/secret-key (if empty, --length=50 --exclude-punctuation)
#   - secret authentik/bootstrap.email
#   - secret authentik/bootstrap.password
#   - secret authentik/database.username
#   - secret authentik/database.password
#   - secret authentik/smtp.username
#   - secret authentik/smtp.password
#   pass those to subprocesses for create-hosted-zone and write-secret


if __name__ == "__main__":
    pass
