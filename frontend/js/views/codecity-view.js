/**
 * CodeCity — Three.js 3D codebase visualization.
 * Files → buildings (height = LOC, color = language, footprint = complexity).
 * Activated via view selector.
 */
class CodeCityView {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.controls = null;
        this._initialized = false;
        this._data = null;
    }

    _ensureThree() {
        if (typeof THREE !== 'undefined') return;
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.min.js';
        document.head.appendChild(script);
        const orbit = document.createElement('script');
        orbit.src = 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/js/controls/OrbitControls.js';
        document.head.appendChild(orbit);
    }

    init() {
        if (this._initialized) return;
        this._ensureThree();

        const W = this.container.clientWidth;
        const H = this.container.clientHeight;

        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0x0d1117);
        this.scene.fog = new THREE.Fog(0x0d1117, 50, 200);

        this.camera = new THREE.PerspectiveCamera(55, W / Math.max(H, 1), 1, 500);
        this.camera.position.set(60, 50, 60);
        this.camera.lookAt(0, 0, 0);

        this.renderer = new THREE.WebGLRenderer({ antialias: true });
        this.renderer.setSize(W, H);
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        this.renderer.shadowMap.enabled = true;
        this.container.appendChild(this.renderer.domElement);

        // Orbit controls
        this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.08;
        this.controls.maxPolarAngle = Math.PI / 2.2;

        // Lighting
        const ambient = new THREE.AmbientLight(0x404060, 1.8);
        this.scene.add(ambient);
        const sun = new THREE.DirectionalLight(0xffffff, 2.5);
        sun.position.set(80, 100, 60);
        sun.castShadow = true;
        sun.shadow.mapSize.set(2048, 2048);
        sun.shadow.camera.near = 1;
        sun.shadow.camera.far = 300;
        sun.shadow.camera.left = -100; sun.shadow.camera.right = 100;
        sun.shadow.camera.top = 100; sun.shadow.camera.bottom = -100;
        this.scene.add(sun);

        // Ground plane
        const groundGeo = new THREE.PlaneGeometry(200, 200);
        const groundMat = new THREE.MeshPhongMaterial({
            color: 0x1a1a2e, specular: 0x111111, shininess: 10
        });
        const ground = new THREE.Mesh(groundGeo, groundMat);
        ground.rotation.x = -Math.PI / 2;
        ground.position.y = -0.1;
        ground.receiveShadow = true;
        this.scene.add(ground);

        // Grid helper
        const grid = new THREE.GridHelper(200, 40, 0x333355, 0x222244);
        this.scene.add(grid);

        this._initialized = true;
        this._animate();
    }

    setData(nodes, edges) {
        this._data = { nodes, edges };
        if (!this._initialized) this.init();
        this._buildCity();
    }

    _buildCity() {
        // Remove old buildings
        while (this.scene.children.length > 5) {
            const obj = this.scene.children[5];
            if (obj.geometry) obj.geometry.dispose();
            if (obj.material) obj.material.dispose();
            this.scene.remove(obj);
        }

        if (!this._data || !this._data.nodes) return;
        const nodes = this._data.nodes;
        const maxLoc = Math.max(...nodes.map(n => (n.lines_of_code || n.loc || 10)), 1);
        const maxFilesPerDir = Math.max(...this._bucketByDir(nodes).map(b => b.length), 1);

        const colors = {
            python: 0x3572A5, javascript: 0xF7DF1E, typescript: 0x3178C6,
            rust: 0xDEA584, go: 0x00ADD8, 'c++': 0xF34B7D, c: 0x555555,
            java: 0xB07219, default: 0x58A6FF,
        };

        const dirs = this._bucketByDir(nodes);
        let col = 0;
        this._labelSprites = [];

        dirs.forEach((dirNodes, dirIdx) => {
            let row = 0;
            dirNodes.forEach(node => {
                const loc = node.lines_of_code || node.loc || 10;
                const height = Math.max(0.3, (loc / maxLoc) * 15);
                const w = 1.2;
                const d = 1.2;
                const x = col * 3 - (dirs.length * 1.5);
                const z = row * 3 - (dirNodes.length * 1.5);

                const ext = (node.file_path || '').split('.').pop().toLowerCase();
                const langMap = { py: 'python', js: 'javascript', ts: 'typescript',
                    rs: 'rust', go: 'go', cpp: 'c++', hpp: 'c++', java: 'java' };
                const lang = langMap[ext] || 'default';
                const color = colors[lang] || colors.default;

                const geo = new THREE.BoxGeometry(w, height, d);
                const mat = new THREE.MeshPhongMaterial({
                    color, specular: 0x222222, shininess: 20,
                    emissive: new THREE.Color(color).multiplyScalar(0.15),
                });
                const building = new THREE.Mesh(geo, mat);
                building.position.set(x, height / 2, z);
                building.castShadow = true;
                building.receiveShadow = true;
                building.userData = { node, loc, lang };
                this.scene.add(building);

                // Window dots effect
                for (let wy = 0.5; wy < height; wy += 0.8) {
                    const dotGeo = new THREE.SphereGeometry(0.06, 4, 4);
                    const dotMat = new THREE.MeshBasicMaterial({ color: 0xffdd88 });
                    const dot = new THREE.Mesh(dotGeo, dotMat);
                    dot.position.set(x + 0.5, wy, z + 0.55);
                    this.scene.add(dot);
                }

                row++;
            });
            col++;
        });
    }

    _bucketByDir(nodes) {
        const buckets = new Map();
        nodes.forEach(n => {
            const dir = (n.file_path || '').split('/').slice(0, -1).join('/') || '(root)';
            if (!buckets.has(dir)) buckets.set(dir, []);
            buckets.get(dir).push(n);
        });
        return Array.from(buckets.values());
    }

    _animate() {
        if (!this._initialized) return;
        requestAnimationFrame(() => this._animate());
        this.controls.update();
        this.renderer.render(this.scene, this.camera);
    }

    resize() {
        if (!this.renderer) return;
        const W = this.container.clientWidth;
        const H = this.container.clientHeight;
        this.camera.aspect = W / Math.max(H, 1);
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(W, H);
    }
}
