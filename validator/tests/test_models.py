from blindgaming_registry_validator.models import Diagnostic, ValidationReport


def test_validation_report_is_ok_when_it_has_no_error_diagnostics():
    report = ValidationReport(
        snapshot=None,
        diagnostics=(Diagnostic("warning", "registry/games/example.json", "/name", "notice"),),
    )

    assert report.ok is True


def test_validation_report_is_not_ok_when_it_has_an_error_diagnostic():
    report = ValidationReport(
        snapshot=None,
        diagnostics=(Diagnostic("error", "registry/games/example.json", "/name", "invalid"),),
    )

    assert report.ok is False
