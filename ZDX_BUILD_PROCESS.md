# Open-Pyxel Development Build Process

## Purpose

Defines the required workflow for all Open-Pyxel development passes.

Priority:

security > correctness > maintainability > features

## Build Pass Workflow

### 0. Review TODO and Architecture

Before coding:
- Review current TODO documents.
- Review existing implementation patterns.
- Identify compatibility risks.
- Update TODO after each completed pass.
- Keep documentation synchronized.

### 1. Code Intent

Before implementation document:
- purpose of the change
- affected systems
- security impact
- risks and dependencies

### 2. Build Map Approval

Define:
- implementation steps
- files affected
- tests required
- documentation updates
- failure scenarios

Only execute after approval.

### 3. Implementation

Rules:
- Preserve working systems.
- Avoid unnecessary rewrites.
- Make focused commits.
- Add tests with new behavior.

### 4. Verification Report

After each pass report:
- completed changes
- commits
- tests
- unexpected issues
- technical debt
- documentation status

### 5. Security Gate

For identity, network, protocol, or compute changes review:
- authentication
- authorization
- input validation
- replay protection
- abuse cases
- failure handling

### 6. Architecture Review

Major milestones require:
- code review
- architecture review
- simplification review
- red-team review
- documentation review

## Required Future Change Flow

1. Proposal
2. Build map
3. Implementation
4. Unit tests
5. Integration tests
6. Security review
7. Red-team review
8. Documentation update
9. Merge

## Current Security Milestone

Active work:
- cryptographic node identity
- signed protocol messages
- authenticated sessions
- replay prevention
- revocation support
- enrollment control

After security completion:
Pause feature expansion and perform architecture/threat-model review before continuing development.

## Stable Release Process

When stable:
- introduce versioning
- maintain changelog
- archive obsolete documentation
- maintain release notes
