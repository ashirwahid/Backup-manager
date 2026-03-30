# MS Graph Policy Manager - PRD

## Original Problem Statement
Create a webapp that uses Microsoft Graph API to deploy JSONs and export policies from the Microsoft portal as JSON in the tenant and store it in Azure DevOps. Export Intune Device Configuration Policies, Conditional Access Policies, and Compliance Policies.

## User Choices
- All policy types: Device Configuration, Conditional Access, Compliance, Configuration (Settings Catalog)
- Azure DevOps integration
- Light theme, minimal dashboard
- Need guidance on Azure AD App Registration setup
- CIS Baseline comparison with 4 columns (Tenant Only, Baseline Only, Conflicting, Matching)
- GitHub repo for CIS baseline storage

## Architecture
- **Backend**: FastAPI + MongoDB
- **Frontend**: React + Tailwind CSS + Shadcn UI
- **Integrations**: MS Graph API, Azure DevOps REST API, GitHub API

## User Personas
1. **IT Administrator** - Needs to export and backup Intune policies
2. **DevOps Engineer** - Needs to version control policies in Azure DevOps
3. **Security Analyst** - Needs to compare tenant with CIS baseline for compliance

## Core Requirements (Static)
- Export policies from MS Graph API
- Store exports in MongoDB
- Push policies to Azure DevOps repository
- Settings management for credentials
- CIS Baseline comparison with GitHub integration

## What's Been Implemented - 2026-03-24
### Backend
- MS Graph API client with OAuth 2.0 client credentials flow
- Support for 4 policy types: Device Configuration, Configuration, Conditional Access, Compliance
- Azure DevOps client for Git operations (push files, create commits)
- GitHub client for fetching CIS baseline policies
- Policy comparison logic (tenant vs baseline)
- Settings API with credential storage in MongoDB
- Export history tracking with sync status
- Dashboard statistics endpoint

### Frontend
- Dashboard with stat cards and recent exports table
- Policies page with 4 policy type cards and export functionality
- **CIS Comparison page** with 4-column comparison view:
  - Tenant Only (policies only in your tenant)
  - Baseline Only (policies only in CIS baseline)
  - Conflicting (same policy, different settings - shows diff)
  - Matching (identical policies)
- DevOps Sync page with export history and push to DevOps
- Settings page with Azure AD, DevOps, and GitHub configuration
- JSON viewer with syntax highlighting
- Responsive sidebar navigation

## Prioritized Backlog
### P0 (Critical) - DONE
- [x] Basic MS Graph authentication
- [x] Policy export endpoints
- [x] Azure DevOps push functionality
- [x] Settings management
- [x] CIS Baseline comparison with GitHub

### P1 (Important)
- [ ] Add support for importing/deploying policies back to Intune
- [ ] Add scheduled/automated exports
- [ ] Add export to CIS baseline (push differences to GitHub)

### P2 (Nice to Have)
- [ ] Policy templates
- [ ] Audit logging
- [ ] Multi-tenant support
- [ ] Email notifications for sync status
- [ ] Compliance scoring based on baseline match

## Next Tasks
1. User needs to configure Azure AD App Registration
2. User needs to create Azure DevOps PAT
3. User needs to configure GitHub repo URL for CIS baseline
4. Test full comparison flow with real CIS baseline JSONs
