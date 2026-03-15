import { Link, useLocation } from "react-router-dom";
import { cn } from "../lib/utils";
import {
    Database,
    Search,
    Library,
    LayoutDashboard,
    BookOpen,
    Menu,
    X,
    Puzzle,
    LogOut,
    User as UserIcon
} from "lucide-react";
import { useState } from "react";
import { authService } from "../lib/auth";

export function AdminLayout({ children }: { children: React.ReactNode }) {
    const location = useLocation();
    const [isOpen, setIsOpen] = useState(false);

    const navigation = [
        { name: "Dashboard", href: "/admin", icon: LayoutDashboard },
        { name: "Index Repo", href: "/admin/index", icon: Database },
        { name: "Repositories", href: "/admin/repos", icon: Library },
        { name: "Catalogs", href: "/admin/catalogs", icon: BookOpen },
        { name: "Playbook Composer", href: "/admin/playbook-composer", icon: Puzzle },
    ];

    return (
        <div className="min-h-screen bg-gray-50 flex">
            {/* Mobile sidebar toggle */}
            <button
                className="lg:hidden fixed top-4 left-4 z-50 p-2 bg-white rounded-md shadow"
                onClick={() => setIsOpen(!isOpen)}
            >
                {isOpen ? <X /> : <Menu />}
            </button>

            {/* Sidebar */}
            <div className={cn(
                "fixed inset-y-0 left-0 z-40 w-64 bg-white border-r border-gray-200 transform transition-transform duration-200 ease-in-out lg:translate-x-0 lg:static lg:block",
                isOpen ? "translate-x-0" : "-translate-x-full"
            )}>
                <div className="h-16 flex items-center px-6 border-b border-gray-200 justify-between">
                    <span className="text-xl font-bold text-primary">Discovery Agent</span>
                </div>
                {/* User Info */}
                <div className="p-4 border-b border-gray-100 bg-gray-50/50">
                    <div className="flex items-center gap-3 px-2">
                        <div className="w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center">
                            <UserIcon className="w-4 h-4 text-primary" />
                        </div>
                        <div className="flex-1 min-w-0">
                            <p className="text-xs font-bold text-gray-900 truncate">
                                {authService.getUser()?.full_name || "User"}
                            </p>
                            <p className="text-[10px] text-gray-400 font-bold uppercase tracking-wider">
                                {authService.getUser()?.role || "Member"}
                            </p>
                        </div>
                    </div>
                </div>
                <nav className="p-4 space-y-1">
                    {navigation.map((item) => {
                        const isActive = location.pathname === item.href;
                        return (
                            <Link
                                key={item.name}
                                to={item.href}
                                className={cn(
                                    "flex items-center px-4 py-3 text-sm font-medium rounded-lg transition-colors",
                                    isActive
                                        ? "bg-primary/10 text-primary"
                                        : "text-gray-600 hover:bg-gray-100 hover:text-gray-900"
                                )}
                            >
                                <item.icon className="w-5 h-5 mr-3" />
                                {item.name}
                            </Link>
                        );
                    })}
                    <div className="pt-4 mt-4 border-t border-gray-200">
                        <Link
                            to="/catalog-search"
                            className="flex items-center px-4 py-3 text-sm font-medium text-gray-600 rounded-lg hover:bg-gray-100 hover:text-gray-900"
                        >
                            <Search className="w-5 h-5 mr-3" />
                            Catalog Search
                        </Link>
                    </div>
                    <div className="pt-4 mt-auto border-t border-gray-200 p-4">
                        <button
                            onClick={() => authService.logout()}
                            className="w-full flex items-center px-4 py-3 text-sm font-medium text-rose-600 rounded-lg hover:bg-rose-50 transition-colors"
                        >
                            <LogOut className="w-5 h-5 mr-3" />
                            Sign Out
                        </button>
                    </div>
                </nav>
            </div>

            {/* Main content */}
            <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
                <main className="flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8">
                    {children}
                </main>
            </div>
        </div>
    );
}

export function UserLayout({ children }: { children: React.ReactNode }) {
    return (
        <div className="min-h-screen bg-gray-50">
            <header className="bg-white border-b border-gray-200 shadow-sm">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
                    <div className="flex items-center gap-8">
                        <span className="text-xl font-bold text-primary">Discovery Agent</span>
                        <nav className="hidden md:flex gap-4">
                            <Link to="/catalog-search" className="text-sm font-medium text-gray-600 hover:text-primary">
                                Catalog Search
                            </Link>
                            <Link to="/reasoning-lab" className="text-sm font-medium text-gray-600 hover:text-primary">
                                Reasoning Lab
                            </Link>
                            <Link to="/playbook-store" className="text-sm font-medium text-gray-600 hover:text-primary">
                                PlaybookStore
                            </Link>
                        </nav>
                    </div>
                    <div className="flex items-center gap-6">
                        <Link to="/admin" className="text-sm font-bold text-gray-500 hover:text-gray-900">
                            Admin Portal
                        </Link>
                        <div className="flex items-center gap-3 pl-6 border-l border-gray-100">
                            <div className="hidden sm:block text-right">
                                <p className="text-xs font-bold text-gray-900">
                                    {authService.getUser()?.full_name}
                                </p>
                                <button 
                                    onClick={() => authService.logout()}
                                    className="text-[10px] font-black uppercase tracking-widest text-gray-400 hover:text-rose-500 transition-colors"
                                >
                                    Log Out
                                </button>
                            </div>
                            <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center">
                                <UserIcon className="w-4 h-4 text-primary" />
                            </div>
                        </div>
                    </div>
                </div>
            </header>
            <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
                {children}
            </main>
        </div>
    );
}
