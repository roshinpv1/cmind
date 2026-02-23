import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import { AdminLayout, UserLayout } from "./layouts/Layout";
import RepoList from "./pages/admin/RepoList";
import RepoIndex from "./pages/admin/RepoIndex";
import CatalogCreate from "./pages/admin/CatalogCreate";
import AgentCatalogSearch from "./pages/user/AgentCatalogSearch";
import ChatInterface from "./pages/user/ChatInterface";

function App() {
  return (
    <Router>
      <Routes>
        {/* Main Entry Points */}
        <Route path="/" element={<Navigate to="/catalog-search" replace />} />
        <Route path="/catalog-search" element={<UserLayout><AgentCatalogSearch /></UserLayout>} />
        <Route path="/reasoning-lab" element={<UserLayout><ChatInterface /></UserLayout>} />

        {/* Admin Routes */}
        <Route path="/admin" element={<AdminLayout><RepoList /></AdminLayout>} />
        <Route path="/admin/repos" element={<AdminLayout><RepoList /></AdminLayout>} />
        <Route path="/admin/index" element={<AdminLayout><RepoIndex /></AdminLayout>} />
        <Route path="/admin/catalog/create" element={<AdminLayout><CatalogCreate /></AdminLayout>} />

        {/* Catch all */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Router>
  );
}

export default App;
