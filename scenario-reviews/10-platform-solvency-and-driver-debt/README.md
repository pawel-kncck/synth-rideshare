# LLM review instructions

Review **Scenario 10: Three-Way Platform War with Endogenous Solvency and Driver Vehicle Debt (Difficult)**, described in [DESCRIPTION.md](DESCRIPTION.md), against the existing implementation of this simulator. The repository root is two directories above this folder.

This is an analysis task. Write your findings in this folder as `ANALYSIS-{MODEL}.md`, replacing `{MODEL}` with the model identifier used for the review (use a filesystem-safe spelling). Record the full model identifier, review date, and repository revision or working-tree state at the top of the report. If your model identifier is unavailable, use `UNKNOWN` and state that explicitly. Preserve reports from other models.

## Review process

1. Read the complete scenario, including its observable checks and modeling challenge. Treat it as a set of requirements to evaluate, not as a description of features already implemented.
2. Follow applicable repository instructions. Start with the repository README, then inspect relevant documentation, scenario examples, and implementation. Trace capabilities through actual execution paths; do not rely on names or documentation alone.
3. Cover every material requirement: setup, participant behavior, timing, state changes, interactions, and observability. Separate the ability to configure a scenario from the ability to reproduce its intended mechanisms and measure its checks. Distinguish configurable support from approximations and from changes requiring code.
4. Cite concrete repository paths and relevant symbols or line numbers for findings. Identify uncertainty and missing evidence explicitly. If you perform a small verification, report what you ran and observed; otherwise describe conclusions as based on inspection. Running the full scenario is not required.
5. Flag ambiguous or contradictory requirements and state any assumptions needed for your assessment. Do not silently rewrite the scenario or treat a desired business outcome as guaranteed by configuration.
6. Write only the analysis report as the review deliverable. Do not implement fixes, modify simulator code or these input files, or create a runnable scenario as part of this review. Form your assessment independently of other models' reports.

## Required report structure

Use the following four numbered sections. If a category has no findings, say so explicitly.

### 1. What is currently possible

Identify requirements the existing implementation supports. Explain how each can be expressed using current configuration, scenario format, or existing interfaces, and what evidence supports that conclusion. State any limits or assumptions. Include whether the requested observable checks can be evaluated from current logs or metrics.

### 2. What is almost possible with a small, quick, independent adjustment

Identify requirements needing a localized change that can stand on its own without a new subsystem or broad refactor. For each, explain the current gap, the smallest proposed adjustment, affected files or interfaces, why the adjustment is small and independent, and how it could be verified. Do not place a requirement here if it depends on substantial undeveloped capabilities; put it in section 3 instead.

### 3. What is not currently possible and what must be developed

Identify requirements requiring substantial new capabilities or architectural changes. Explain the missing behavior or state, the components that would need to be developed or extended, key dependencies, and how success could be checked. If an approximation exists, explain which parts of the original requirement it loses. Distinguish an implementation limitation from an unresolved scenario requirement.

### 4. Documentation quality and code readability

Assess how easy it was to discover the simulator's capabilities, limitations, and scenario format. Explain which documentation, examples, types, or code paths helped; where explanations were missing, misleading, scattered, or hard to follow; and where code readability affected confidence in your findings. Give specific references and actionable documentation or readability suggestions. Separate documentation gaps from missing functionality.
