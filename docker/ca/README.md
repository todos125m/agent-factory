# Extra trusted root certificates (optional)

If an antivirus or a company proxy **inspects HTTPS** on your machine (AVG/Avast "Web Shield", Kaspersky, Zscaler...), the
image build fails with `CERTIFICATE_VERIFY_FAILED` when it downloads Python packages: the inspector signs sites with its own
root certificate, which your computer trusts but the build does not.

Put that root certificate here as a PEM file ending in `.crt` (e.g. `docker/ca/avg.crt`) and build again; it is added to the
image's trust store (`update-ca-certificates`) and used by pip and curl. Files here (except this README) are never committed.
Or switch off HTTPS scanning in the antivirus while you build.

Export on Windows (PowerShell), for AVG as an example:

    $c = Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -like "*AVG Web/Mail Shield Root*" } | Select-Object -First 1
    "-----BEGIN CERTIFICATE-----`n" + [Convert]::ToBase64String($c.RawData, "InsertLineBreaks") + "`n-----END CERTIFICATE-----" | Set-Content docker/ca/avg.crt -Encoding ascii
