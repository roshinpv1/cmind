/**
 * Frontend Authentication Service for CodeMind.
 * Handles JWT storage, login via SSO, and authenticated request headers.
 */

export interface User {
  user_id: string;
  email: string;
  full_name: string;
  role: string;
}

class AuthService {
  private tokenKey = "codemind_auth_token";
  private userKey = "codemind_user";

  login(token: string, user: User) {
    localStorage.setItem(this.tokenKey, token);
    localStorage.setItem(this.userKey, JSON.stringify(user));
  }

  logout() {
    localStorage.removeItem(this.tokenKey);
    localStorage.removeItem(this.userKey);
    window.location.href = "/login";
  }

  getToken(): string | null {
    return localStorage.getItem(this.tokenKey);
  }

  getUser(): User | null {
    const user = localStorage.getItem(this.userKey);
    return user ? JSON.parse(user) : null;
  }

  isAuthenticated(): boolean {
    return !!this.getToken();
  }

  getAuthHeader(): Record<string, string> {
    const token = this.getToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  /**
   * Simulated SSO Login. 
   * In a real enterprise app, this would redirect to the SSO provider.
   */
  async mockSSOLogin(email: string, name: string) {
    const mockIdToken = JSON.stringify({
      sub: email.replace(/[@.]/g, "_"),
      email: email,
      name: name,
      role: email.includes("admin") ? "admin" : "user",
      dept: "Engineering"
    });

    try {
      const resp = await fetch("/api/v1/auth/sso-login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id_token: mockIdToken })
      });

      if (!resp.ok) throw new Error("SSO Login failed");
      
      const data = await resp.json();
      this.login(data.access_token, data.user);
      return data.user;
    } catch (err) {
      console.error(err);
      throw err;
    }
  }
}

export const authService = new AuthService();
