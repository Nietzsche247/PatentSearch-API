// @ts-check
// `@type` JSDoc annotations allow editor autocompletion and type checking
// (when paired with `@ts-check`).
// There are various equivalent ways to declare your Docusaurus config.
// See: https://docusaurus.io/docs/api/docusaurus-config

import {themes as prismThemes} from 'prism-react-renderer';

/** @type {import('@docusaurus/types').Config} */
const config = {
    title: 'PatentsView Search Platform Documentation',
    favicon: 'img/favicon.ico',

    // Set the production url of your site here
    url: 'https://search.patentsview.org',
    // Set the /<baseUrl>/ pathname under which your site is served
    // For GitHub pages deployment, it is often '/<projectName>/'
    baseUrl: '/docs',
    // blog: false,
    // GitHub pages deployment config.
    // If you aren't using GitHub pages, you don't need these.
    organizationName: 'PatentsView', // Usually your GitHub org/user name.
    projectName: 'PatentsView Search Platform', // Usually your repo name.

    onBrokenLinks: 'log',
    onBrokenMarkdownLinks: 'warn',

    // Even if you don't use internationalization, you can use this field to set
    // useful metadata like html lang. For example, if your site is Chinese, you
    // may want to replace "en" with "zh-Hans".
    i18n: {
        defaultLocale: 'en',
        locales: ['en'],
    },

    presets: [
        [
            'classic',

            /** @type {import('@docusaurus/preset-classic').Options} */
            ({
                docs: {
                    sidebarPath: './sidebars.js',
                    // Please change this to your repo.
                    // Remove this to remove the "edit this page" links.
                    // editUrl:
                    //     'https://github.com/facebook/docusaurus/tree/main/packages/create-docusaurus/templates/shared/',
                },
                blog: {
                    showReadingTime: true,
                    blogTitle: 'PatentSearch API Updates',
                    routeBasePath: '/',
                    // Please change this to your repo.
                    // Remove this to remove the "edit this page" links.
                    // editUrl:
                    //     'https://github.com/facebook/docusaurus/tree/main/packages/create-docusaurus/templates/shared/',
                },
                theme: {
                    customCss: './src/css/custom.css',
                },
            }),
        ],

    ],
    plugins: [
        [
            '@docusaurus/plugin-google-gtag',
            {
                trackingID: 'G-K4PTTLH074',
                anonymizeIP: false,
            },
        ],
    ],
    themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
        ({
            // Replace with your project's social card
            image: 'img/PV Social Preview.jpeg',
            navbar: {
                // title: 'PatentsView Search Platform',
                logo: {
                    alt: 'PatentsView',
                    src: 'img/pv-logo.png',
                    href: 'https://www.patentsview.org'
                },
                items: [
                    {
                        to: '/',
                        label: 'PatentSearch API Updates',
                        position: 'left'
                    }, {
                        type: 'docSidebar',
                        sidebarId: 'tutorialSidebar',
                        position: 'left',
                        label: 'PatentSearch API Reference',
                    }, {
                        href: 'https://www.patentsview.org/',
                        label: 'PatentsView',
                        position: 'right',
                    }, {
                        href: 'https://search.patentsview.org/swagger-ui',
                        label: 'Swagger UI',
                        position: 'right',
                    },
                    // Removing the status page link from the navbar as per USPTO request - PV-1915
                    // {
                    //     href: 'https://patentsview.statuspage.io/',
                    //     label: 'PatentsView Service Status',
                    //     position: 'right',
                    // },
                ],
            },
            footer: {
                style: 'dark',
                links: [
                    {
                        title: 'Docs',
                        items: [
                            {
                                label: 'PatentSearch API Updates',
                                to: '/docs',
                            },
                            {
                                label: 'PatentSearch API Reference',
                                to: '/docs/docs/Search%20API/SearchAPIReference',
                            },
                            {
                                label: 'Endpoint Dictionary',
                                to: '/docs/docs/Search API/EndpointDictionary',
                            },
                            {
                                label: 'Long Text Availability',
                                to: '/docs/docs/Search API/TextEndpointStatus',
                            },
                            {
                                label: 'Swagger UI',
                                to: 'https://search.patentsview.org/swagger-ui/',
                            },
                        ],
                    },
                    {
                        title: 'Support',
                        items: [],
                    },
                    {
                        title: 'More',
                        items: [
                            // {
                            //     label: 'Blog',
                            //     to: '/blog',
                            // },
                            {
                                label: 'GitHub',
                                href: 'https://github.com/PatentsView',
                            },
                        ],
                    },
                ],
                copyright: `PatentSearch API data content is licensed under a <a href="https://creativecommons.org/licenses/by/4.0/">Creative Commons Attribution 4.0 License</a><br/><img src="img/cc by 4 badge.png" alt="badge for CC-BY license" width=10%><br/>©${new Date().getFullYear()} PatentsView All Rights Reserved. Built with Docusaurus.`,
            },
            prism: {
                theme: prismThemes.github,
                darkTheme: prismThemes.dracula,
            },
        }),
};

export default config;
