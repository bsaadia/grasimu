import numpy as np
from numpy import sqrt, arctan, log  # import sqrt and arctan function
from numpy import power as p  # Allows for element-wise power
from numpy import multiply as m  # Allows element-wise multiplication
from numpy import divide as d  # Allows for element-wise division
from scipy.ndimage import gaussian_filter
import pandas as pd
from scipy import interpolate
from randomfield.randomq512 import mainfunc
from randomfield.rando2asc import bin2asc
import pyvista as pv


class Scene:
    def __init__(self, name):
        self.name = name
        self.scene_properties = dict(model_bounds=[],
                                     scene_bounds=[],
                                     edge_distance_factor=[],
                                     resolution=[],
                                     datum=[])
        self.target_geometry = dict(mesh=dict(indices=[],
                                              vertices=[],
                                              centre=[],
                                              pv_model=None),
                                    voxel=dict(indices=[],
                                               vertices=[],
                                               vertices_filled=[],
                                               centre=[],
                                               resolution=None),
                                    wireframe=[])
        self.target_parameters = dict(density=[],
                                      gravity=[],
                                      measured_gravity=[])
        self.terrain = dict(true=dict(elevation=[],
                                      gravity=[]),
                            dem=dict(elevation=[],
                                     gravity=[]))
        self.corrections = dict(free_air=[],
                                terrain=[])
        self.data = dict(perfect_gravity=dict(terrain=None,
                                              dem=None,
                                              target=None,
                                              full=None),
                         noisy_gravity=dict(target=None,
                                            full=None),
                         interp_gravity=dict(target=None,
                                             full=None),
                         corrected_gravity=dict(full=None,
                                                interp=None),
                         elevation=dict(terrain=[],
                                        dem=None))
        self.measurements = dict(locations=dict(target=[],
                                                full=[]),
                                 points=dict(target=[list(), list(), list()],
                                             full=[list(), list(), list()],
                                             raw=[list(), list(), list()]))
        self.sim_params = {'x position': None,
                           'y position': None,
                           'Calculation Resolution': None,
                           'Voxel Resolution': None,
                           'Target Density Contrast': None,
                           'Target Depth': None,
                           'Terrain Seed': None,
                           'Correlation Length, x': None,
                           'Correlation Length, y': None,
                           'Max Elevation': None,
                           'Min Elevation': None,
                           'DTM Error': None,
                           'Background/Terrain Density': None,
                           'Gravimeter Error': None,
                           'GPS Error': None}

    def create_datum(self, resolution, extent_multiplier, extent_x1=None, extent_y1=None, extent_x2=None, extent_y2=None):
        # ONE DAY INCLUDE OPTION TO AUTO SET EXTENT BASED ON MODEL BOUNDS
        # self.scene_properties['scene_bounds'] = extent_multiplier * self.scene_properties['model_bounds']
        # mesh_centre = self.target_geometry['mesh']['centre']
        # self.scene_properties['scene_bounds'] = mesh_centre[0]
        # x = np.arange(self.scene_properties['scene_bounds'][0],
        #               self.scene_properties['scene_bounds'][1],
        #               resolution)
        # y = np.arange(self.scene_properties['scene_bounds'][2],
        #               self.scene_properties['scene_bounds'][3],
        #               resolution)
        x = np.arange(extent_x1,
                      extent_x2,
                      resolution)
        y = np.arange(extent_y1,
                      extent_y2,
                      resolution)
        if len(x) >= len(y):
            dim = x
        elif len(x) < len(y):
            dim = y
        xx, yy = np.meshgrid(dim, dim)
        zz = np.zeros_like(xx)
        self.scene_properties['datum'] = [xx, yy, zz]
        self.scene_properties['resolution'] = resolution
        self.sim_params['Calculation Resolution'] = str(resolution)

    def render_mesh(self,
                    centre_depth,
                    centre_x,
                    centre_y,
                    path=None,
                    shape='sphere',
                    radius=50,
                    length=100, ):
        """Imports an stl file or creates a simple mesh."""
        if path:
            mesh = pv.read(path)
        elif shape == 'sphere':
            mesh = pv.Sphere(radius=radius)
        elif shape == 'cylinder':
            mesh = pv.Cylinder(radius=radius, height=length)
            mesh = mesh.triangulate()

        mesh.translate(np.array([-1, -1, -1]) * np.array([mesh.center[0], mesh.center[1], mesh.bounds[5]]))
        mesh.translate(np.array([centre_x, centre_y, centre_depth]))
        faces = mesh.faces
        vertices = mesh.points
        vertices_per_cell = faces[0]
        indices = faces.reshape(-1, vertices_per_cell + 1)[:, 1:vertices_per_cell + 1]
        self.target_geometry['mesh']['indices'] = indices
        self.target_geometry['mesh']['vertices'] = vertices
        self.target_geometry['mesh']['pv_model'] = mesh
        self.sim_params['Target Depth'] = str(centre_depth) + ' m'

    def voxelize_mesh(self, resolution):
        """Creates a voxel model of the stored scene mesh data."""
        mesh = self.target_geometry['mesh']['pv_model']
        vox = pv.voxelize(mesh, resolution, check_surface=False)
        vox_surface = vox.extract_surface()
        vox_mesh = vox_surface.triangulate()
        faces = (vox_surface.faces, vox_mesh.faces)
        vertices = (vox_surface.points, vox_mesh.points, vox.cell_centers().points)

        # get the i, j, k for the vertices of the mesh, voxel surface, and voxel mesh
        indices = list()
        for i in faces:
            vertices_per_cell = i[0]
            indices.append(i.reshape(-1, vertices_per_cell + 1)[:, 1:vertices_per_cell + 1])

        # extract the lists of x, y, z coordinates of the triangle vertices and connect them by a line
        x_wire, y_wire, z_wire = [], [], []
        for T in vertices[0][indices[0]]:
            x_wire.extend([T[k % 4][0] for k in range(5)] + [float('nan')])
            y_wire.extend([T[k % 4][1] for k in range(5)] + [float('nan')])
            z_wire.extend([T[k % 4][2] for k in range(5)] + [float('nan')])

        self.target_geometry['voxel']['indices'] = indices[1]
        self.target_geometry['voxel']['vertices'] = vertices[1]
        self.target_geometry['voxel']['vertices_filled'] = vertices[2]
        self.target_geometry['wireframe'] = np.array([x_wire, y_wire, z_wire])
        self.target_geometry['voxel']['resolution'] = resolution
        self.scene_properties['model_bounds'] = np.round(vox.bounds, 0)
        self.sim_params['Voxel Resolution'] = str(resolution) + ' m'

    def generate_terrain(self, method, x_corr_len, y_corr_len, max_elevation, min_elevation, seed, path=None):
        extent = self.scene_properties['datum']
        if method == 6:
            data_xyz = pd.read_csv(path, names=["x", "y", "z"], delimiter='\s')
            size = int(np.sqrt(len(data_xyz)))
            data = np.array(data_xyz['z']).reshape((size, size))
            self.data['elevation']['terrain'] = [self.scene_properties['datum'][0].ravel(),
                                                 self.scene_properties['datum'][1].ravel(),
                                                 np.array(data_xyz['z']),
                                                 data]  # shouldn't repeat this
            # self.scene_properties['datum'][0] = np.array(data_xyz['x'])
            # self.scene_properties['datum'][1] = np.array(data_xyz['y'])
            print(len(self.scene_properties['datum'][0].ravel()), len(self.scene_properties['datum'][1].ravel()))
            print(len(np.array(data_xyz['z'])))

        else:
            with open('grasimu_project/inputFile', 'r') as file:
                filedata = file.read()

            dimx = len(extent[0])
            dimy = len(extent[1])

            filedata = filedata.replace('method', str(method))
            filedata = filedata.replace('corr_len', str(x_corr_len) + ',' + str(y_corr_len))

            filedata = filedata.replace('seed', str(seed))
            filedata = filedata.replace('dimx', str(dimx))
            filedata = filedata.replace('dimy', str(dimy))

            # Write the file out again
            with open('randinq', 'w') as file:
                file.write(filedata)

            mainfunc()  # Generates binary file rando
            bin2asc()  # converts to a text file

            data = pd.read_csv('randout', sep='\s+', names=['X', 'Y', 'Z'])
            data = np.reshape([data['Z']], (1024, 1024))
            data = data[0:dimx, 0:dimy]
            minimum, maximum = np.min(data), np.max(data)

            m = (max_elevation - min_elevation) / (maximum - minimum)
            b = min_elevation - m * minimum
            z_true = m * data + b
            z_flat = z_true.ravel()
            self.data['elevation']['terrain'] = [self.scene_properties['datum'][0].ravel(),
                                                 self.scene_properties['datum'][1].ravel(),
                                                 z_flat,
                                                 z_true]  # shouldn't repeat this

        self.sim_params['Terrain Seed'] = str(seed)
        self.sim_params['Correlation Length, x'] = str(x_corr_len) + ' m'
        self.sim_params['Correlation Length, y'] = str(y_corr_len) + ' m'
        self.sim_params['Max Elevation'] = str(max_elevation) + ' m'
        self.sim_params['Min Elevation'] = str(min_elevation) + ' m'

    def generate_dem(self, err):
        signal = self.data['elevation']['terrain'][2]
        error = gaussian_filter(signal, sigma=1)
        dim = int(np.sqrt(len(signal)))

        minimum, maximum = np.min(signal), np.max(signal)
        max_elevation = err
        min_elevation = -err

        m = (max_elevation - min_elevation) / (maximum - minimum)
        b = min_elevation - m * minimum
        z_flat = m * error + b + signal
        z = z_flat.reshape(dim, dim)

        self.data['elevation']['dem'] = [self.scene_properties['datum'][0].ravel(),
                                         self.scene_properties['datum'][1].ravel(),
                                         z_flat,
                                         z]

        self.corrections['free_air'] = [self.scene_properties['datum'][0].ravel(),
                                        self.scene_properties['datum'][1].ravel(),
                                        0.03086 * z_flat,
                                        0.03086 * z]

        self.sim_params['DTM Error'] = '+/- ' + str(err) + ' m'

    def calculate_terrain_gravity(self, rho):
        for terrain_type in ['terrain', 'dem']:
            terrain_height = np.array(self.data['elevation'][terrain_type][2])

            x = self.scene_properties['datum'][0]
            y = self.scene_properties['datum'][1]
            dim = np.sqrt(len(terrain_height)).astype(int)
            terrain_height = terrain_height.reshape(dim, dim)
            #   CONSTANTS
            G = 6.67e-11  # Gravitational constant, m^3*kg^-1*s^-2
            h2 = 0
            #   INITIALIZE VARIABLES
            cell_size = self.scene_properties['resolution']
            del_a = cell_size ** 2
            total_g = 0

            #   CALCULATION
            for i in range(len(x)):
                for j in range(len(y)):
                    x_dist = x[i, j] - x
                    y_dist = y[i, j] - y
                    z_dist = terrain_height[i, j] - terrain_height

                    term1 = np.sqrt((np.square(x_dist) + np.square(y_dist) + np.square(z_dist)))
                    term2 = np.sqrt((np.square(x_dist) + np.square(y_dist) + np.square(h2)))
                    del_g = G * rho * del_a * (1 / term2 - 1 / term1)  # Gravitational accl from rectangular prism
                    del_g[np.isnan(del_g)] = 0  # g cannot be analytically obtained for the point being operated on
                    total_g = total_g + del_g
            g = (total_g * 1e5)
            g_flat = g.ravel()
            self.data['perfect_gravity'][terrain_type] = [self.scene_properties['datum'][0].ravel(),
                                                          self.scene_properties['datum'][1].ravel(),
                                                          g_flat,
                                                          g]
        self.corrections['terrain'] = [self.scene_properties['datum'][0].ravel(),
                                       self.scene_properties['datum'][1].ravel(),
                                       -self.data['perfect_gravity']['dem'][2],
                                       -self.data['perfect_gravity']['dem'][3]]
        self.sim_params['Background/Terrain Density'] = str(rho) + 'kg/m^3'

    def calculate_target_gravity(self, density_contrast, with_terrain=False, with_noise=False, grav_err=0, gps_err=0):
        def single_voxel_gravity(drho, x_cen, y_cen, z_cen, spacing, x, y, z):
            x1 = x_cen - spacing / 2
            x2 = x_cen + spacing / 2
            y1 = y_cen - spacing / 2
            y2 = y_cen + spacing / 2
            z1 = -1 * (z_cen + spacing / 2)
            z2 = -1 * (z_cen - spacing / 2)

            dx1 = x1 - x
            dx2 = x2 - x
            dy1 = y1 - y
            dy2 = y2 - y
            dz1 = z1 - z
            dz2 = z2 - z

            # Define gravitational constant in mGal m^2/kg
            G = (6.67408e-11) * 1e5

            R111 = sqrt(p(dx1, 2) + p(dy1, 2) + p(dz1, 2))
            R112 = sqrt(p(dx2, 2) + p(dy1, 2) + p(dz1, 2))
            R121 = sqrt(p(dx1, 2) + p(dy2, 2) + p(dz1, 2))
            R122 = sqrt(p(dx2, 2) + p(dy2, 2) + p(dz1, 2))
            R211 = sqrt(p(dx1, 2) + p(dy1, 2) + p(dz2, 2))
            R212 = sqrt(p(dx2, 2) + p(dy1, 2) + p(dz2, 2))
            R221 = sqrt(p(dx1, 2) + p(dy2, 2) + p(dz2, 2))
            R222 = sqrt(p(dx2, 2) + p(dy2, 2) + p(dz2, 2))

            g111 = -(m(dz1, arctan(d(m(dx1, dy1), m(dz1, R111)))) - m(dx1, log(R111 + dy1)) - m(dy1,
                                                                                                log(R111 + dx1)))
            g112 = (m(dz1, arctan(d(m(dx2, dy1), m(dz1, R112)))) - m(dx2, log(R112 + dy1)) - m(dy1,
                                                                                               log(R112 + dx2)))
            g121 = (m(dz1, arctan(d(m(dx1, dy2), m(dz1, R121)))) - m(dx1, log(R121 + dy2)) - m(dy2,
                                                                                               log(R121 + dx1)))
            g122 = -(m(dz1, arctan(d(m(dx2, dy2), m(dz1, R122)))) - m(dx2, log(R122 + dy2)) - m(dy2,
                                                                                                log(R122 + dx2)))

            g211 = (m(dz2, arctan(d(m(dx1, dy1), m(dz2, R211)))) - m(dx1, log(R211 + dy1)) - m(dy1,
                                                                                               log(R211 + dx1)))
            g212 = -(m(dz2, arctan(d(m(dx2, dy1), m(dz2, R212)))) - m(dx2, log(R212 + dy1)) - m(dy1,
                                                                                                log(R212 + dx2)))
            g221 = -(m(dz2, arctan(d(m(dx1, dy2), m(dz2, R221)))) - m(dx1, log(R221 + dy2)) - m(dy2,
                                                                                                log(R221 + dx1)))
            g222 = (m(dz2, arctan(d(m(dx2, dy2), m(dz2, R222)))) - m(dx2, log(R222 + dy2)) - m(dy2,
                                                                                               log(R222 + dx2)))

            dg = drho * G * (g111 + g112 + g121 + g122 + g211 + g212 + g221 + g222)
            return dg

        def add_noise(data, noise, seed=1):
            np.random.seed(seed)
            err = np.random.normal(0, noise / 2, (len(data), len(data)))
            err_sum = np.round(data + err, 2)
            return err_sum

        self.target_parameters['density'] = density_contrast
        self.sim_params['Target Density Contrast'] = str(density_contrast) + ' kg/m^3'

        x_pt = self.target_geometry['voxel']['vertices_filled'][:, 0]
        y_pt = self.target_geometry['voxel']['vertices_filled'][:, 1]
        z_pt = self.target_geometry['voxel']['vertices_filled'][:, 2]

        x_loc = self.scene_properties['datum'][0]
        y_loc = self.scene_properties['datum'][1]

        if not with_terrain:
            z_loc = self.scene_properties['datum'][2]
            terrain_key = 'target'
            background = 0
        else:
            z_loc = self.data['elevation']['terrain'][3]
            terrain_key = 'full'
            background = self.data['perfect_gravity']['terrain'][3]

        if with_noise:
            x_loc = add_noise(x_loc, gps_err)
            y_loc = add_noise(y_loc, gps_err)
            noise_key = 'noisy_gravity'
            k = 1
        else:
            noise_key = 'perfect_gravity'
            k = 0

        g = 0
        for i in range(0, len(x_pt)):
            single_point_gravity = single_voxel_gravity(density_contrast,
                                                        x_pt[i], y_pt[i], z_pt[i],
                                                        self.target_geometry['voxel']['resolution'],
                                                        x_loc, y_loc, z_loc)
            g += single_point_gravity
        g = [g, add_noise(g, grav_err)]

        g_val = g[k] + background
        g_flat = g_val.ravel()

        self.data[noise_key][terrain_key] = [self.scene_properties['datum'][0].ravel(),
                                             self.scene_properties['datum'][1].ravel(),
                                             g_flat,
                                             g_val]

    def update_survey(self, x, y, z, grav_err, gps_err):
        def add_noise(data, noise, seed=1):
            np.random.seed(seed)
            err = np.random.normal(0, noise / 2, len(data))
            err_sum = np.round(data + err, 2)
            return err_sum

        xx = self.scene_properties['datum'][0]
        yy = self.scene_properties['datum'][1]
        self.sim_params['Gravimeter Error'] = str(grav_err) + ' mgal'
        self.sim_params['GPS Error'] = str(gps_err) + ' m'

        x_ind = []
        y_ind = []
        for i in x:
            idx = np.where(xx == i)[1][0]
            x_ind.append(idx)
        for j in y:
            idy = np.where(yy == j)[0][0]
            y_ind.append(idy)

        x_noise = add_noise(x, gps_err)
        y_noise = add_noise(y, gps_err)

        g_test = (self.data['perfect_gravity']['target'][3])
        g_from_terrain = self.data['perfect_gravity']['full'][3]

        self.measurements['points']['target'] = g_test[y_ind, x_ind]
        self.measurements['points']['full'] = g_from_terrain[y_ind, x_ind]
        self.measurements['points']['raw'] = add_noise(g_test[y_ind, x_ind], grav_err)

        self.measurements['locations']['target'] = [x, y]
        self.measurements['locations']['full'] = [x, y]
        self.measurements['locations']['raw'] = [x_noise, y_noise]

    def interpolate_survey_pts(self, method):
        for key in self.measurements['points'].keys():
            xx = (self.scene_properties['datum'][0])
            yy = self.scene_properties['datum'][1]
            vals = self.measurements['points'][key]
            x = self.measurements['locations'][key][0]
            y = self.measurements['locations'][key][1]

            interpolated_measurements = interpolate.griddata(points=tuple([x, y]),
                                                             values=vals,
                                                             xi=(xx, yy),
                                                             method=method)

            int_flat = interpolated_measurements.ravel()

            self.data['interp_gravity'][key] = [self.scene_properties['datum'][0].ravel(),
                                                self.scene_properties['datum'][1].ravel(),
                                                int_flat,
                                                interpolated_measurements]

    def apply_corrections(self, free_air=False, terrain=False):

        if free_air:
            fac = self.corrections['free_air'][3]
        else:
            fac = np.zeros_like(self.corrections['free_air'][3])

        if terrain:
            tc = self.corrections['terrain'][3]
        else:
            tc = np.zeros_like(self.corrections['terrain'][3])

        correction = fac + tc

        self.data['corrected_gravity']['full'] = [None,
                                                  None,
                                                  None,
                                                  self.data['noisy_gravity']['full'][3] + correction]
        self.data['corrected_gravity']['interp'] = [None,
                                                    None,
                                                    None,
                                                    np.add(self.data['interp_gravity']['full'][3],
                                                           correction,
                                                           where=(self.data['interp_gravity']['full'][3] != None))]
