import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import { AdminLayout, UserLayout } from "./layouts/Layout";
import RepoList from "./pages/admin/RepoList";
import RepoIndex from "./pages/admin/RepoIndex";
import CatalogCreate from "./pages/admin/CatalogCreate";
import CatalogList from "./pages/admin/CatalogList";
import RepoEdit from "./pages/admin/RepoEdit";
import ProposalCreate from "./pages/admin/ProposalCreate";
import Dashboard from "./pages/admin/Dashboard";
import AgentCatalogSearch from "./pages/user/AgentCatalogSearch";
import ChatInterface from "./pages/user/ChatInterface";
import PlaybookStore from "./pages/user/PlaybookStore";
import PlaybookComposer from "./pages/admin/PlaybookComposer";

function App() {
  return (
    <Router>
      <Routes>
        {/* Main Entry Points */}
        <Route path="/" element={<Navigate to="/catalog-search" replace />} />
        <Route path="/catalog-search" element={<UserLayout><AgentCatalogSearch /></UserLayout>} />
        <Route path="/reasoning-lab" element={<UserLayout><ChatInterface /></UserLayout>} />
        <Route path="/playbook-store" element={<UserLayout><PlaybookStore /></UserLayout>} />

        {/* Admin Routes */}
        <Route path="/admin" element={<AdminLayout><Dashboard /></AdminLayout>} />
        <Route path="/admin/dashboard" element={<AdminLayout><Dashboard /></AdminLayout>} />
        <Route path="/admin/repos" element={<AdminLayout><RepoList /></AdminLayout>} />
        <Route path="/admin/repos/:repoId/edit" element={<AdminLayout><RepoEdit /></AdminLayout>} />
        <Route path="/admin/index" element={<AdminLayout><RepoIndex /></AdminLayout>} />
        <Route path="/admin/catalogs" element={<AdminLayout><CatalogList /></AdminLayout>} />
        <Route path="/admin/catalog/create" element={<AdminLayout><CatalogCreate /></AdminLayout>} />
        <Route path="/admin/catalogs/propose" element={<AdminLayout><ProposalCreate /></AdminLayout>} />
        <Route path="/admin/playbook-composer" element={<AdminLayout><PlaybookComposer /></AdminLayout>} />
        <Route path="/admin/playbook-composer/:id" element={<AdminLayout><PlaybookComposer /></AdminLayout>} />

        {/* Catch all */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Router>
  );
}

export default App;

