# MS Graph Policy Manager - PRD

## Original Problem Statement
Create a webapp that uses Microsoft Graph API to deploy JSONs and export policies from the Microsoft portal as JSON in the tenant and store it in Azure DevOps. Export Intune Device Configuration Policies, Conditional Access Policies, and Compliance Policies.

## User Choices
- All policy types: Device Configuration, Conditional Access, Compliance, Configuration (Settings Catalog)
- Azure DevOps integration
- Light theme, minimal dashboard
- Need guidance on Azure AD App Registration setup

## Architecture
- **Backend**: FastAPI + MongoDB
- **Frontend**: React + Tailwind CSS + Shadcn UI
- **Integrations**: MS Graph API, Azure DevOps REST API

## User Personas
1. **IT Administrator** - Needs to export and backup Intune policies
2. **DevOps Engineer** - Needs to version control policies in Azure DevOps

## Core Requirements (Static)
- Export policies from MS Graph API
- Store exports in MongoDB
- Push policies to Azure DevOps repository
- Settings management for credentials

## What's Been Implemented - 2026-03-24
### Backend
- MS Graph API client with OAuth 2.0 client credentials flow
- Support for 4 policy types: Device Configuration, Configuration, Conditional Access, Compliance
- Azure DevOps client for Git operations (push files, create commits)
- Settings API with credential storage in MongoDB
- Export history tracking with sync status
- Dashboard statistics endpoint

### Frontend
- Dashboard with stat cards and recent exports table
- Policies page with 4 policy type cards and export functionality
- DevOps Sync page with export history and push to DevOps
- Settings page with Azure AD and DevOps configuration
- JSON viewer with syntax highlighting
- Responsive sidebar navigation

## Prioritized Backlog
### P0 (Critical)
- [x] Basic MS Graph authentication
- [x] Policy export endpoints
- [x] Azure DevOps push functionality
- [x] Settings management

### P1 (Important)
- [ ] Add support for importing/deploying policies back to Intune
- [ ] Add scheduled/automated exports
- [ ] Add policy comparison (diff view)

### P2 (Nice to Have)
- [ ] Policy templates
- [ ] Audit logging
- [ ] Multi-tenant support
- [ ] Email notifications for sync status

## Next Tasks
1. User needs to configure Azure AD App Registration:
   - Create app in Azure Portal > Azure AD > App registrations
   - Grant permissions: DeviceManagementConfiguration.Read.All, Policy.Read.All
   - Create client secret
   
2. User needs to create Azure DevOps PAT:
   - Go to Azure DevOps > User Settings > Personal Access Tokens
   - Create token with Code (Read & Write) scope
   
3. User needs to create/select a repository in Azure DevOps for policy storage
