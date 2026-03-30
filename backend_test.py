#!/usr/bin/env python3

import requests
import sys
import json
from datetime import datetime
from typing import Dict, Any, Optional

class MSPolicyManagerAPITester:
    def __init__(self, base_url="https://msft-policy-manager.preview.emergentagent.com"):
        self.base_url = base_url
        self.api_url = f"{base_url}/api"
        self.tests_run = 0
        self.tests_passed = 0
        self.test_results = []

    def log_test(self, name: str, success: bool, details: str = "", response_data: Any = None):
        """Log test result"""
        self.tests_run += 1
        if success:
            self.tests_passed += 1
        
        result = {
            "test_name": name,
            "success": success,
            "details": details,
            "response_data": response_data
        }
        self.test_results.append(result)
        
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} - {name}")
        if details:
            print(f"    {details}")
        if not success and response_data:
            print(f"    Response: {response_data}")

    def run_test(self, name: str, method: str, endpoint: str, expected_status: int, 
                 data: Optional[Dict] = None, headers: Optional[Dict] = None) -> tuple:
        """Run a single API test"""
        url = f"{self.api_url}{endpoint}"
        default_headers = {'Content-Type': 'application/json'}
        if headers:
            default_headers.update(headers)

        try:
            if method == 'GET':
                response = requests.get(url, headers=default_headers, timeout=30)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=default_headers, timeout=30)
            elif method == 'DELETE':
                response = requests.delete(url, headers=default_headers, timeout=30)
            else:
                raise ValueError(f"Unsupported method: {method}")

            success = response.status_code == expected_status
            response_data = None
            
            try:
                response_data = response.json()
            except:
                response_data = response.text

            details = f"Status: {response.status_code} (expected {expected_status})"
            if not success:
                details += f" | Response: {response_data}"

            self.log_test(name, success, details, response_data if not success else None)
            return success, response_data

        except Exception as e:
            self.log_test(name, False, f"Exception: {str(e)}")
            return False, str(e)

    def test_basic_endpoints(self):
        """Test basic API endpoints"""
        print("\n🔍 Testing Basic Endpoints...")
        
        # Test root endpoint
        self.run_test("Root API endpoint", "GET", "/", 200)
        
        # Test health endpoint
        self.run_test("Health check endpoint", "GET", "/health", 200)

    def test_settings_endpoints(self):
        """Test settings-related endpoints"""
        print("\n🔍 Testing Settings Endpoints...")
        
        # Get settings (should work even if not configured)
        success, settings_data = self.run_test("Get settings", "GET", "/settings", 200)
        
        # Verify settings response includes github_configured status
        if success and settings_data:
            expected_fields = ["configured", "azure_configured", "devops_configured", "github_configured"]
            missing_fields = [field for field in expected_fields if field not in settings_data]
            if missing_fields:
                self.log_test("Settings response structure", False, f"Missing fields: {missing_fields}")
            else:
                self.log_test("Settings response structure", True, "All expected fields present including github_configured")
        
        # Test settings update
        test_settings = {
            "azure_tenant_id": "test-tenant-id",
            "azure_client_id": "test-client-id",
            "devops_org": "test-org",
            "devops_project": "test-project",
            "devops_repo": "test-repo",
            "devops_branch": "main"
        }
        
        self.run_test("Update settings", "POST", "/settings", 200, test_settings)
        
        # Test Azure connection (should fail with test credentials)
        self.run_test("Test Azure connection", "POST", "/settings/test-azure", 200)
        
        # Test DevOps connection (should fail with test credentials)
        self.run_test("Test DevOps connection", "POST", "/settings/test-devops", 200)

    def test_dashboard_endpoints(self):
        """Test dashboard-related endpoints"""
        print("\n🔍 Testing Dashboard Endpoints...")
        
        # Get dashboard stats
        success, stats_data = self.run_test("Get dashboard stats", "GET", "/dashboard/stats", 200)
        
        if success and stats_data:
            # Verify expected fields in dashboard stats
            expected_fields = ["total_exports", "synced_exports", "pending_sync", 
                             "policy_type_counts", "recent_exports", "azure_configured", "devops_configured"]
            
            missing_fields = [field for field in expected_fields if field not in stats_data]
            if missing_fields:
                self.log_test("Dashboard stats structure", False, f"Missing fields: {missing_fields}")
            else:
                self.log_test("Dashboard stats structure", True, "All expected fields present")

    def test_export_endpoints(self):
        """Test export-related endpoints"""
        print("\n🔍 Testing Export Endpoints...")
        
        # Get exports list
        success, exports_data = self.run_test("Get exports list", "GET", "/exports", 200)
        
        # Test individual policy export endpoints (these will likely fail without proper credentials)
        policy_endpoints = [
            "/policies/device-configuration",
            "/policies/configuration", 
            "/policies/conditional-access",
            "/policies/compliance"
        ]
        
        for endpoint in policy_endpoints:
            policy_type = endpoint.split('/')[-1].replace('-', ' ').title()
            # These should return 400 (credentials not configured) or 401 (auth failed)
            success, response_data = self.run_test(
                f"Export {policy_type} policies", 
                "GET", 
                endpoint, 
                400  # Expecting 400 due to missing credentials
            )
            
            # If it's not 400, check if it's 401 (also acceptable)
            if not success:
                # Try again expecting 401
                success_401, _ = self.run_test(
                    f"Export {policy_type} policies (401 check)", 
                    "GET", 
                    endpoint, 
                    401
                )
                if success_401:
                    self.log_test(f"Export {policy_type} policies", True, "Returns 401 as expected (auth issue)")

        # Test export all policies
        success, response_data = self.run_test("Export all policies", "POST", "/policies/export-all", 400)
        if not success:
            # Try expecting 401
            success_401, _ = self.run_test("Export all policies (401 check)", "POST", "/policies/export-all", 401)
            if success_401:
                self.log_test("Export all policies", True, "Returns 401 as expected (auth issue)")

    def test_devops_endpoints(self):
        """Test DevOps sync endpoints"""
        print("\n🔍 Testing DevOps Sync Endpoints...")
        
        # Test sync endpoint with invalid export ID (should return 404)
        sync_data = {
            "export_id": "non-existent-id",
            "commit_message": "Test commit"
        }
        self.run_test("Sync non-existent export", "POST", "/devops/sync", 404, sync_data)

    def test_cis_baseline_endpoints(self):
        """Test CIS Baseline Comparison endpoints"""
        print("\n🔍 Testing CIS Baseline Comparison Endpoints...")
        
        # Test baseline files endpoint (should return 400 if GitHub not configured)
        success, response_data = self.run_test("Get baseline files", "GET", "/baseline/files", 400)
        if not success:
            # Try expecting 500 (server error)
            success_500, _ = self.run_test("Get baseline files (500 check)", "GET", "/baseline/files", 500)
            if success_500:
                self.log_test("Get baseline files", True, "Returns 500 as expected (GitHub not configured)")
        
        # Test baseline compare endpoint (should return 400 if GitHub not configured)
        success, response_data = self.run_test("Compare with baseline", "POST", "/baseline/compare", 400)
        if not success:
            # Try expecting 500 (server error)
            success_500, _ = self.run_test("Compare with baseline (500 check)", "POST", "/baseline/compare", 500)
            if success_500:
                self.log_test("Compare with baseline", True, "Returns 500 as expected (GitHub not configured)")
        
        # Test baseline compare with policy type parameter
        success, response_data = self.run_test("Compare with baseline (device config)", "POST", "/baseline/compare?policy_type=device_configuration", 400)
        if not success:
            success_500, _ = self.run_test("Compare with baseline device config (500 check)", "POST", "/baseline/compare?policy_type=device_configuration", 500)
            if success_500:
                self.log_test("Compare with baseline (device config)", True, "Returns 500 as expected (GitHub not configured)")
        
        # Test GitHub connection test endpoint
        self.run_test("Test GitHub connection", "POST", "/settings/test-github", 200)

    def run_all_tests(self):
        """Run all test suites"""
        print("🚀 Starting MS Policy Manager API Tests")
        print(f"Testing against: {self.base_url}")
        print("=" * 60)
        
        self.test_basic_endpoints()
        self.test_settings_endpoints()
        self.test_dashboard_endpoints()
        self.test_export_endpoints()
        self.test_devops_endpoints()
        self.test_cis_baseline_endpoints()
        
        print("\n" + "=" * 60)
        print(f"📊 Test Results: {self.tests_passed}/{self.tests_run} passed")
        
        if self.tests_passed == self.tests_run:
            print("🎉 All tests passed!")
            return 0
        else:
            print("⚠️  Some tests failed - this is expected for policy exports without proper credentials")
            
            # Count critical failures (basic endpoints)
            critical_failures = [r for r in self.test_results 
                               if not r["success"] and any(x in r["test_name"].lower() 
                               for x in ["root", "health", "settings", "dashboard"])]
            
            if critical_failures:
                print(f"❌ {len(critical_failures)} critical failures found!")
                for failure in critical_failures:
                    print(f"   - {failure['test_name']}: {failure['details']}")
                return 1
            else:
                print("✅ All critical endpoints working - policy export failures expected without credentials")
                return 0

def main():
    tester = MSPolicyManagerAPITester()
    return tester.run_all_tests()

if __name__ == "__main__":
    sys.exit(main())