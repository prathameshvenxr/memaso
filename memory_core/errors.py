class MemasoError(Exception):
    pass


class LLMSchemaError(MemasoError):
    pass


class ExtractionFailed(MemasoError):
    pass


class AuthError(MemasoError):
    pass


class BudgetExceeded(MemasoError):
    pass
